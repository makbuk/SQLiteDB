"""
create_db.py — create a SQLite database from a schema file.

Usage:
    python create_db.py                          # default: schema.sql -> database.db
    python create_db.py --schema my.sql          # custom schema file
    python create_db.py --db my.db               # custom database file
    python create_db.py --schema s.sql --db d.db # both arguments
"""

import sqlite3
import argparse
import sys
from pathlib import Path


def create_database(schema_path: Path, db_path: Path) -> None:
    """Create a SQLite database by executing the given schema file."""

    # --- Validate the schema file ---
    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {schema_path}")
        sys.exit(1)

    sql = schema_path.read_text(encoding="utf-8").strip()
    if not sql:
        print(f"[ERROR] Schema file is empty: {schema_path}")
        sys.exit(1)

    # --- Create (or reopen) the database ---
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()

    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(sql)   # execute the entire schema in one shot
            conn.commit()
    except sqlite3.Error as exc:
        print(f"[ERROR] SQLite: {exc}")
        sys.exit(1)

    action = "updated" if existed else "created"
    print(f"[OK] Database {action}: {db_path.resolve()}")

    # --- List all objects created in the database ---
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'view', 'index', 'trigger') "
            "ORDER BY type, name"
        )
        rows = cur.fetchall()

    if rows:
        print("\nDatabase objects:")
        for obj_type, name in rows:
            print(f"  [{obj_type}] {name}")
    else:
        print("\nDatabase is empty — the schema contains no objects.")


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

    create_database(Path(args.schema), Path(args.db))


if __name__ == "__main__":
    main()
