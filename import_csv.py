"""
import_csv.py — import one or more CSV files into a SQLite database.

For each CSV file the script:
  - Maps CSV columns to existing table columns (by name, case-insensitive).
  - Skips CSV columns that have no matching column in the table.
  - Inserts rows in configurable batches for performance.
  - Supports three conflict strategies: skip, replace, fail.
  - Reports inserted / skipped / failed counts per file.

Usage:
    # Import single CSV — table name derived from file name
    python import_csv.py users.csv --db database.db

    # Explicit table name
    python import_csv.py report.csv --db database.db --table users

    # Multiple CSVs (table names derived from file names)
    python import_csv.py users.csv orders.csv --db database.db

    # On duplicate primary key: replace existing row
    python import_csv.py users.csv --db database.db --on-conflict replace

    # On duplicate primary key: abort with error
    python import_csv.py users.csv --db database.db --on-conflict fail

    # Preview first N rows without writing anything
    python import_csv.py users.csv --db database.db --dry-run

    # Larger batch size (default: 500)
    python import_csv.py big.csv --db database.db --batch 2000
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def csv_to_table_name(csv_path: Path) -> str:
    """Derive a safe SQL table name from a CSV file name."""
    stem = csv_path.stem
    name = re.sub(r"[^\w]", "_", stem).strip("_").lower()
    name = re.sub(r"_+", "_", name)
    if name and name[0].isdigit():
        name = "t_" + name
    return name or "table"


def detect_delimiter(csv_path: Path) -> str:
    """Sniff the delimiter from the first 4 KB of the file."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","  # safe default


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for the given table (empty list if table not found)."""
    try:
        cur = conn.execute(f"PRAGMA table_info('{table}')")
        return [row[1] for row in cur.fetchall()]
    except sqlite3.Error:
        return []


def normalise(name: str) -> str:
    """Lowercase and strip a name for case-insensitive matching."""
    return name.strip().lower()


def build_column_mapping(
    csv_headers: list[str],
    db_columns: list[str],
) -> dict[int, str]:
    """
    Map CSV column indices to DB column names (case-insensitive).
    Returns {csv_index: db_column_name} for matched columns only.
    """
    db_lower = {normalise(c): c for c in db_columns}
    mapping: dict[int, str] = {}
    for i, header in enumerate(csv_headers):
        key = normalise(header)
        if key in db_lower:
            mapping[i] = db_lower[key]
    return mapping


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def import_csv(
    csv_path: Path,
    conn: sqlite3.Connection,
    table: str,
    on_conflict: str,  # "skip" | "replace" | "fail"
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int, int]:
    """
    Import rows from csv_path into `table`.
    Returns (inserted, skipped, failed).
    """
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        sys.exit(1)

    # Check the table exists in the DB
    db_columns = get_table_columns(conn, table)
    if not db_columns:
        print(f"[ERROR] Table '{table}' not found in database. "
              f"Run csv_to_schema.py + create_db.py first.")
        sys.exit(1)

    delimiter = detect_delimiter(csv_path)

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, delimiter=delimiter)

        try:
            csv_headers = next(reader)
        except StopIteration:
            print(f"[WARN]  {csv_path.name}: empty file, nothing to import.")
            return 0, 0, 0

        mapping = build_column_mapping(csv_headers, db_columns)

        if not mapping:
            print(f"[ERROR] No CSV columns match table '{table}' columns.")
            print(f"  CSV columns : {csv_headers}")
            print(f"  DB  columns : {db_columns}")
            sys.exit(1)

        # Report column mapping
        matched_csv   = [csv_headers[i] for i in mapping]
        unmatched_csv = [h for i, h in enumerate(csv_headers) if i not in mapping]
        print(f"  Matched    : {matched_csv}")
        if unmatched_csv:
            print(f"  Ignored    : {unmatched_csv}")

        if dry_run:
            print(f"  [DRY-RUN] First 5 rows that would be inserted:")
            for n, row in enumerate(reader):
                if n >= 5:
                    break
                record = {mapping[i]: (row[i] if i < len(row) else None)
                          for i in mapping}
                print(f"    {record}")
            return 0, 0, 0

        # Build INSERT statement
        db_cols_ordered = [mapping[i] for i in sorted(mapping)]
        placeholders    = ", ".join("?" * len(db_cols_ordered))
        col_list        = ", ".join(db_cols_ordered)

        or_clause = {"skip": "OR IGNORE", "replace": "OR REPLACE", "fail": ""}[on_conflict]
        sql = f"INSERT {or_clause} INTO {table} ({col_list}) VALUES ({placeholders})"

        inserted = skipped = failed = 0
        batch: list[tuple] = []

        def flush(batch: list[tuple]) -> tuple[int, int, int]:
            nonlocal inserted, skipped, failed
            ins = sk = fa = 0
            if on_conflict == "fail":
                try:
                    conn.executemany(sql, batch)
                    ins = len(batch)
                except sqlite3.IntegrityError as exc:
                    print(f"  [ERROR] Integrity error: {exc}")
                    fa = len(batch)
            else:
                before = conn.execute("SELECT changes()").fetchone()[0]
                # executemany with OR IGNORE/REPLACE handles conflicts silently
                for row_vals in batch:
                    try:
                        conn.execute(sql, row_vals)
                        ins += 1
                    except sqlite3.IntegrityError:
                        fa += 1
            return ins, sk, fa

        for raw_row in reader:
            values = tuple(
                (raw_row[i].strip() if i < len(raw_row) else None)
                for i in sorted(mapping)
            )
            # Treat empty strings as NULL
            values = tuple(v if v != "" else None for v in values)
            batch.append(values)

            if len(batch) >= batch_size:
                i2, s2, f2 = flush(batch)
                inserted += i2; skipped += s2; failed += f2
                batch.clear()

        if batch:
            i2, s2, f2 = flush(batch)
            inserted += i2; skipped += s2; failed += f2

        conn.commit()

    return inserted, skipped, failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import CSV file(s) into a SQLite database"
    )
    parser.add_argument(
        "csv_files", nargs="+", metavar="CSV",
        help="CSV file(s) to import"
    )
    parser.add_argument(
        "--db", required=True, metavar="FILE",
        help="Path to the SQLite database file"
    )
    parser.add_argument(
        "--table", "-t", metavar="NAME",
        help="Target table name (only valid with a single input file)"
    )
    parser.add_argument(
        "--on-conflict", choices=["skip", "replace", "fail"], default="skip",
        help="What to do when a row conflicts with an existing record "
             "(default: skip)"
    )
    parser.add_argument(
        "--batch", type=int, default=500, metavar="N",
        help="Rows per INSERT batch (default: 500)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show column mapping and first 5 rows without writing to DB"
    )
    args = parser.parse_args()

    if args.table and len(args.csv_files) > 1:
        print("[ERROR] --table can only be used with a single input file.")
        sys.exit(1)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        print("  Run create_db.py first to create the database.")
        sys.exit(1)

    # Always back up the database before any writes
    if not args.dry_run:
        from db_utils import make_backup
        make_backup(db_path)

    total_inserted = total_skipped = total_failed = 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        for csv_arg in args.csv_files:
            csv_path   = Path(csv_arg)
            table_name = args.table if args.table else csv_to_table_name(csv_path)

            print(f"\n[{csv_path.name}] -> table '{table_name}'")

            inserted, skipped, failed = import_csv(
                csv_path, conn, table_name,
                on_conflict=args.on_conflict,
                batch_size=args.batch,
                dry_run=args.dry_run,
            )

            if not args.dry_run:
                print(f"  Inserted : {inserted}")
                if skipped:
                    print(f"  Skipped  : {skipped}")
                if failed:
                    print(f"  Failed   : {failed}")

            total_inserted += inserted
            total_skipped  += skipped
            total_failed   += failed

    if not args.dry_run and len(args.csv_files) > 1:
        print(f"\n[TOTAL] inserted={total_inserted}  "
              f"skipped={total_skipped}  failed={total_failed}")

    if not args.dry_run:
        status = "with errors" if total_failed else "successfully"
        print(f"\n[OK] Import completed {status}: {db_path.resolve()}")


if __name__ == "__main__":
    main()
