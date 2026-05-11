"""
create_db.py — create a SQLite database from a schema file.

Usage:
    python create_db.py                          # default: schema.sql -> database.db
    python create_db.py --schema my.sql          # custom schema file
    python create_db.py --db my.db               # custom database file
    python create_db.py --schema s.sql --db d.db # both arguments

If the database file already exists, the script asks interactively:
    1) Replace — back up the current DB and create a fresh one from schema.
    2) Adapt   — back up the current DB, then add missing tables/columns from schema
                 (tables not present in the schema are left untouched).
    3) Cancel  — abort without any changes.

For recurring migrations (option 2 above) use migrate_db.py directly.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from db_utils import list_objects, make_backup, op_adapt, read_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ask_action() -> str:
    """
    Prompt the user to choose what to do with an existing database.
    Returns '1' (replace), '2' (adapt), or '3' (cancel).
    """
    print("\nDatabase already exists. Choose an action:")
    print("  1) Replace — back up current DB and create a fresh one from schema")
    print("  2) Adapt   — back up current DB and apply missing changes from schema")
    print("  3) Cancel  — abort, leave everything unchanged")
    while True:
        choice = input("Enter 1, 2, or 3: ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("  Please enter 1, 2, or 3.")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def op_replace(db_path: Path, sql: str) -> None:
    """Back up the existing DB, delete it, and create a fresh one from schema."""
    make_backup(db_path)
    db_path.unlink()  # remove the old file
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(sql)
            conn.commit()
    except sqlite3.Error as exc:
        print(f"[ERROR] SQLite: {exc}")
        sys.exit(1)

    print(f"[OK] Database replaced: {db_path.resolve()}")
    list_objects(db_path)


def op_create_new(db_path: Path, sql: str) -> None:
    """Create a brand-new database from schema (no existing DB)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(sql)
            conn.commit()
    except sqlite3.Error as exc:
        print(f"[ERROR] SQLite: {exc}")
        sys.exit(1)
    print(f"[OK] Database created: {db_path.resolve()}")
    list_objects(db_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a SQLite database from a schema file"
    )
    parser.add_argument(
        "--schema", default="schema.sql",
        help="Path to the SQL schema file (default: schema.sql)"
    )
    parser.add_argument(
        "--db", default="database.db",
        help="Path to the SQLite database file (default: database.db)"
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    db_path     = Path(args.db)

    sql = read_schema(schema_path)

    if not db_path.exists():
        # Fresh start — no questions asked
        op_create_new(db_path, sql)
    else:
        choice = ask_action()
        if choice == "1":
            op_replace(db_path, sql)
        elif choice == "2":
            op_adapt(db_path, sql)
        else:
            print("[CANCELLED] No changes were made.")
            sys.exit(0)


if __name__ == "__main__":
    main()
