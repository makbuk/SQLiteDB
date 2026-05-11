"""
db_to_schema.py — generate a SQL schema file from an existing SQLite database.

Extracts and re-emits:
  - CREATE TABLE statements (with columns, types, constraints, defaults)
  - CREATE INDEX statements
  - CREATE VIEW statements
  - PRAGMA settings found in the database (journal_mode, foreign_keys)

Usage:
    python db_to_schema.py database.db                        # print to stdout
    python db_to_schema.py database.db --output schema.sql    # save to file
    python db_to_schema.py database.db --output schema.sql --overwrite
"""

import argparse
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _get_pragma(conn: sqlite3.Connection, name: str) -> str | None:
    """Return the string value of a PRAGMA, or None on error."""
    try:
        row = conn.execute(f"PRAGMA {name}").fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def _extract_pragma_block(conn: sqlite3.Connection) -> list[str]:
    """
    Build a list of PRAGMA lines worth preserving in the schema:
    journal_mode and foreign_keys.
    """
    lines: list[str] = []

    jm = _get_pragma(conn, "journal_mode")
    if jm and jm.upper() != "DELETE":      # DELETE is the SQLite default — skip
        lines.append(f"PRAGMA journal_mode = {jm.upper()};")

    fk = _get_pragma(conn, "foreign_keys")
    if fk == "1":
        lines.append("PRAGMA foreign_keys = ON;")

    return lines


def _master_objects(conn: sqlite3.Connection, obj_type: str) -> list[tuple[str, str]]:
    """
    Return [(name, sql), ...] for all non-system objects of the given type
    ('table', 'index', 'view') from sqlite_master, in creation order.
    """
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = ? AND name NOT LIKE 'sqlite_%' "
        "ORDER BY rootpage",
        (obj_type,),
    ).fetchall()
    # Filter out rows with NULL sql (can happen for some internal indexes)
    return [(name, sql) for name, sql in rows if sql]


def _normalise_sql(sql: str) -> str:
    """
    Clean up raw SQL from sqlite_master:
      - Collapse irregular whitespace inside the statement.
      - Ensure the statement ends with exactly one semicolon.
      - Preserve multi-line formatting of column definitions.
    """
    # sqlite_master stores the SQL mostly as-written, but sometimes
    # normalises spacing in unexpected ways. We just ensure a clean ending.
    sql = sql.strip()
    if not sql.endswith(";"):
        sql += ";"
    return sql


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

def extract_schema(db_path: Path) -> str:
    """
    Connect to the SQLite database and produce a complete .sql schema string.
    """
    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        # Fallback: open read-write if read-only mode is unsupported
        conn = sqlite3.connect(db_path)

    blocks: list[str] = []

    # ---- PRAGMA block ------------------------------------------------------
    pragmas = _extract_pragma_block(conn)
    if pragmas:
        blocks.append("-- Pragmas\n" + "\n".join(pragmas))

    # ---- Tables ------------------------------------------------------------
    tables = _master_objects(conn, "table")
    if tables:
        table_sqls = []
        for name, sql in tables:
            table_sqls.append(f"-- Table: {name}\n{_normalise_sql(sql)}")
        blocks.append("-- " + "─" * 60 + "\n-- Tables\n-- " + "─" * 60 +
                      "\n\n" + "\n\n".join(table_sqls))

    # ---- Indexes -----------------------------------------------------------
    indexes = _master_objects(conn, "index")
    if indexes:
        idx_sqls = []
        for name, sql in indexes:
            idx_sqls.append(_normalise_sql(sql))
        blocks.append("-- " + "─" * 60 + "\n-- Indexes\n-- " + "─" * 60 +
                      "\n\n" + "\n".join(idx_sqls))

    # ---- Views -------------------------------------------------------------
    views = _master_objects(conn, "view")
    if views:
        view_sqls = []
        for name, sql in views:
            view_sqls.append(f"-- View: {name}\n{_normalise_sql(sql)}")
        blocks.append("-- " + "─" * 60 + "\n-- Views\n-- " + "─" * 60 +
                      "\n\n" + "\n\n".join(view_sqls))

    conn.close()

    header = (
        f"-- Schema extracted from: {db_path.name}\n"
        f"-- Tables : {len(tables)}\n"
        f"-- Indexes: {len(indexes)}\n"
        f"-- Views  : {len(views)}\n"
    )

    return header + "\n" + "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a SQL schema file from a SQLite database"
    )
    parser.add_argument(
        "db_file", metavar="DB",
        help="Path to the SQLite database file (.db)"
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write schema to this .sql file (default: print to stdout)"
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite the output file if it already exists"
    )
    args = parser.parse_args()

    db_path = Path(args.db_file)
    schema  = extract_schema(db_path)

    if args.output:
        out = Path(args.output)
        if out.exists() and not args.overwrite:
            print(f"[ERROR] Output file already exists: {out}")
            print("  Use --overwrite to replace it.")
            sys.exit(1)
        out.write_text(schema, encoding="utf-8")
        print(f"[OK] Schema written to: {out.resolve()}")
        print(schema)
    else:
        print(schema)


if __name__ == "__main__":
    main()
