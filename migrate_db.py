"""
migrate_db.py — apply schema changes to an existing SQLite database without data loss.

What it does:
  - Creates new tables that are present in the schema but missing from the DB.
  - Adds missing columns to existing tables (ALTER TABLE ADD COLUMN).
  - Tables not mentioned in the schema are left completely untouched.
  - A timestamped .bkp backup is created before any changes are applied.

Usage:
    python migrate_db.py                          # default: schema.sql -> database.db
    python migrate_db.py --schema my.sql          # custom schema file
    python migrate_db.py --db my.db               # custom database file
    python migrate_db.py --schema s.sql --db d.db # both arguments
    python migrate_db.py --dry-run                # preview changes, no modifications
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from db_utils import (
    list_objects,
    make_backup,
    op_adapt,
    parse_columns,
    parse_create_tables,
    read_schema,
)


# ---------------------------------------------------------------------------
# Dry-run helper (preview only, no writes)
# ---------------------------------------------------------------------------

def dry_run(db_path: Path, sql: str) -> None:
    """
    Analyse the schema against the current database and print what would change,
    without making any modifications or creating a backup.
    """
    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    print(f"[DRY-RUN] Comparing schema against: {db_path.resolve()}\n")

    schema_tables = parse_create_tables(sql)
    any_change = False

    with sqlite3.connect(db_path) as conn:
        existing_tables = {
            row[0].lower()
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }

        for table_name, create_sql in schema_tables.items():
            if table_name not in existing_tables:
                print(f"  [WOULD CREATE] table '{table_name}'")
                any_change = True
            else:
                existing_columns = {
                    row[1].lower()
                    for row in conn.execute(f"PRAGMA table_info('{table_name}')")
                }
                for col_name, alter_def in parse_columns(create_sql):
                    if col_name not in existing_columns:
                        print(f"  [WOULD ADD]    column '{col_name}' to table '{table_name}'  ({alter_def})")
                        any_change = True

    if any_change:
        print("\nRun without --dry-run to apply these changes.")
    else:
        print("  Database is already up to date — nothing to change.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply schema changes to an existing SQLite database without data loss"
    )
    parser.add_argument(
        "--schema", default="schema.sql",
        help="Path to the SQL schema file (default: schema.sql)"
    )
    parser.add_argument(
        "--db", default="database.db",
        help="Path to the SQLite database file (default: database.db)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without modifying anything"
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    db_path     = Path(args.db)

    sql = read_schema(schema_path)

    if args.dry_run:
        dry_run(db_path, sql)
    else:
        op_adapt(db_path, sql)


if __name__ == "__main__":
    main()
