"""
csv_to_schema.py — generate a SQLite schema file from one or more CSV files.

For each CSV file the script:
  - Reads a sample of rows to infer column types (INTEGER, REAL, TEXT).
  - Detects a likely primary key column (id, <table>_id, or first integer column).
  - Emits a CREATE TABLE IF NOT EXISTS statement.
  - Optionally writes the result to a .sql file.

Usage:
    # Single file — prints schema to stdout
    python csv_to_schema.py data.csv

    # Multiple files — one table per file
    python csv_to_schema.py users.csv orders.csv products.csv

    # Save to a schema file
    python csv_to_schema.py data.csv --output schema.sql

    # Override table name (single file only)
    python csv_to_schema.py report.csv --table my_table

    # Inspect more rows for type inference (default: 500)
    python csv_to_schema.py data.csv --sample 2000

    # Show inferred types without writing anything
    python csv_to_schema.py data.csv --dry-run
"""

import argparse
import csv
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

def _looks_integer(values: list[str]) -> bool:
    """Return True if every non-empty value looks like a whole number."""
    return all(re.fullmatch(r"-?\d+", v) for v in values if v.strip())


def _looks_real(values: list[str]) -> bool:
    """Return True if every non-empty value looks like a decimal number."""
    return all(re.fullmatch(r"-?\d+(\.\d*)?([eE][+-]?\d+)?", v) for v in values if v.strip())


def infer_type(values: list[str]) -> str:
    """
    Infer the SQLite column type from a list of sample string values.
    Priority: INTEGER > REAL > TEXT.
    Empty/null-only columns default to TEXT.
    """
    non_empty = [v.strip() for v in values if v.strip()]
    if not non_empty:
        return "TEXT"
    if _looks_integer(non_empty):
        return "INTEGER"
    if _looks_real(non_empty):
        return "REAL"
    return "TEXT"


# ---------------------------------------------------------------------------
# Primary key detection
# ---------------------------------------------------------------------------

_PK_NAMES = {"id", "pk", "key"}


def detect_pk(columns: list[str], types: dict[str, str], table_name: str) -> str | None:
    """
    Heuristically pick a primary key column:
      1. A column literally named 'id'.
      2. A column named '<table_name>_id' or '<table_name>id'.
      3. Any column whose name ends with '_id' or 'id' and has type INTEGER.
      4. The first INTEGER column.
    Returns the column name, or None if nothing fits.
    """
    col_lower = {c: c.lower() for c in columns}

    # Exact 'id'
    for col in columns:
        if col_lower[col] == "id":
            return col

    # <table>_id or <table>id
    tname = table_name.lower()
    for col in columns:
        cl = col_lower[col]
        if cl in (f"{tname}_id", f"{tname}id"):
            return col

    # Ends with 'id' and is INTEGER
    for col in columns:
        if col_lower[col].endswith("id") and types[col] == "INTEGER":
            return col

    # First INTEGER column
    for col in columns:
        if types[col] == "INTEGER":
            return col

    return None


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

def csv_to_table_name(csv_path: Path) -> str:
    """Derive a safe SQL table name from a file name."""
    stem = csv_path.stem
    # Replace non-alphanumeric characters with underscores
    name = re.sub(r"[^\w]", "_", stem).strip("_").lower()
    # Collapse consecutive underscores
    name = re.sub(r"_+", "_", name)
    # Prefix with 't_' if the name starts with a digit
    if name and name[0].isdigit():
        name = "t_" + name
    return name or "table"


def read_sample(csv_path: Path, sample: int) -> tuple[list[str], list[list[str]]]:
    """
    Read up to `sample` data rows from the CSV.
    Returns (headers, rows) where rows is a list of string lists.
    Raises SystemExit on read errors.
    """
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            # Sniff the dialect from the first 4 KB
            dialect = csv.Sniffer().sniff(fh.read(4096), delimiters=",;\t|")
            fh.seek(0)
            reader = csv.reader(fh, dialect)
            try:
                headers = next(reader)
            except StopIteration:
                print(f"[ERROR] {csv_path.name}: file is empty or has no header row.")
                sys.exit(1)
            rows = []
            for i, row in enumerate(reader):
                if i >= sample:
                    break
                rows.append(row)
        return headers, rows
    except (OSError, csv.Error) as exc:
        print(f"[ERROR] Cannot read {csv_path}: {exc}")
        sys.exit(1)


def build_column_types(headers: list[str], rows: list[list[str]]) -> dict[str, str]:
    """Build a {column_name: sqlite_type} mapping from sampled rows."""
    types: dict[str, str] = {}
    for i, header in enumerate(headers):
        column_values = [row[i] for row in rows if i < len(row)]
        types[header] = infer_type(column_values)
    return types


def generate_create_table(
    table_name: str,
    headers: list[str],
    types: dict[str, str],
) -> str:
    """
    Build a CREATE TABLE IF NOT EXISTS statement.
    The detected primary key column gets PRIMARY KEY AUTOINCREMENT (if INTEGER).
    All other columns are nullable TEXT / INTEGER / REAL.
    A created_at audit column is appended automatically.
    """
    pk_col = detect_pk(headers, types, table_name)
    lines: list[str] = []

    for col in headers:
        safe_col = re.sub(r"[^\w]", "_", col).strip("_") or "col"
        col_type = types[col]

        if col == pk_col and col_type == "INTEGER":
            lines.append(f"    {safe_col:<24} INTEGER  PRIMARY KEY AUTOINCREMENT")
        else:
            lines.append(f"    {safe_col:<24} {col_type}")

    # Append audit timestamp column only if not already present
    audit_col = "created_at"
    if audit_col not in [re.sub(r"[^\w]", "_", h).strip("_").lower() for h in headers]:
        lines.append(f"    {'created_at':<24} TEXT     NOT NULL DEFAULT (datetime('now'))")

    body = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {table_name} (\n{body}\n);\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process_file(
    csv_path: Path,
    table_name: str,
    sample: int,
    dry_run: bool,
) -> str:
    """Analyse one CSV file and return its CREATE TABLE SQL."""
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        sys.exit(1)

    headers, rows = read_sample(csv_path, sample)
    types = build_column_types(headers, rows)

    if dry_run:
        print(f"\n-- {csv_path.name}  ({len(rows)} rows sampled)")
        max_len = max(len(h) for h in headers)
        for h in headers:
            print(f"  {h:<{max_len}}  ->  {types[h]}")
        pk = detect_pk(headers, types, table_name)
        if pk:
            print(f"  * primary key: {pk}")
        return ""

    return generate_create_table(table_name, headers, types)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a SQLite schema from one or more CSV files"
    )
    parser.add_argument(
        "csv_files", nargs="+", metavar="CSV",
        help="CSV file(s) to analyse"
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write schema to this file (default: print to stdout)"
    )
    parser.add_argument(
        "--table", "-t", metavar="NAME",
        help="Override table name (only valid with a single input file)"
    )
    parser.add_argument(
        "--sample", "-s", type=int, default=500, metavar="N",
        help="Number of rows to read for type inference (default: 500)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print inferred column types without generating SQL"
    )
    args = parser.parse_args()

    # Validate --table with multiple files
    if args.table and len(args.csv_files) > 1:
        print("[ERROR] --table can only be used with a single input file.")
        sys.exit(1)

    blocks: list[str] = []

    for csv_arg in args.csv_files:
        csv_path = Path(csv_arg)
        table_name = args.table if args.table else csv_to_table_name(csv_path)
        sql_block = process_file(csv_path, table_name, args.sample, args.dry_run)
        if sql_block:
            header_comment = f"-- Table: {table_name}  (source: {csv_path.name})"
            blocks.append(f"{header_comment}\n{sql_block}")

    if args.dry_run or not blocks:
        return

    schema_sql = "\n".join(blocks)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(schema_sql, encoding="utf-8")
        print(f"[OK] Schema written to: {out_path.resolve()}")
    else:
        print(schema_sql)


if __name__ == "__main__":
    main()
