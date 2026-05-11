"""
db_utils.py — shared utilities for create_db.py and migrate_db.py.

Not intended to be run directly.
"""

import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def read_schema(schema_path: Path) -> str:
    """Read and validate the SQL schema file; return its content."""
    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {schema_path}")
        sys.exit(1)
    sql = schema_path.read_text(encoding="utf-8").strip()
    if not sql:
        print(f"[ERROR] Schema file is empty: {schema_path}")
        sys.exit(1)
    return sql


def make_backup(db_path: Path) -> Path:
    """Copy the database to a timestamped .bkp file and return its path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bkp_path = db_path.with_suffix(f".{timestamp}.bkp")
    shutil.copy2(db_path, bkp_path)
    print(f"[BACKUP] {db_path.name} -> {bkp_path.name}")
    return bkp_path


def list_objects(db_path: Path) -> None:
    """Print all user-created objects (tables, views, indexes, triggers)."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'view', 'index', 'trigger') "
            "  AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
        rows = cur.fetchall()
    if rows:
        print("\nDatabase objects:")
        for obj_type, name in rows:
            print(f"  [{obj_type}] {name}")
    else:
        print("\nDatabase is empty — the schema contains no objects.")


# ---------------------------------------------------------------------------
# Schema parsing
# ---------------------------------------------------------------------------

def parse_create_tables(sql: str) -> dict[str, str]:
    """
    Extract CREATE TABLE statements from the schema SQL.
    Returns a dict: {table_name_lower: full_create_sql}.
    """
    # Strip single-line comments before parsing
    sql_no_comments = re.sub(r"--[^\n]*", "", sql)

    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(.*?\);",
        re.IGNORECASE | re.DOTALL,
    )
    tables: dict[str, str] = {}
    for match in pattern.finditer(sql_no_comments):
        name = match.group(1).lower()
        tables[name] = match.group(0).strip()
    return tables


def parse_columns(create_sql: str) -> list[tuple[str, str]]:
    """
    Parse a CREATE TABLE statement and return a list of (name, alter_def) tuples,
    where alter_def is a safe column definition for use in ALTER TABLE ADD COLUMN.

    Rules applied:
      - Table-level constraints (PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK) are skipped.
      - Inline constraints illegal in ALTER TABLE (NOT NULL, REFERENCES, CHECK) are dropped.
      - DEFAULT clauses are preserved, including expressions like DEFAULT (datetime('now')).
    """
    # Extract the body inside the outermost parentheses
    inner = re.search(r"\((.+)\)\s*;?\s*$", create_sql, re.DOTALL)
    if not inner:
        return []

    # Split on commas that are NOT inside nested parentheses
    body = inner.group(1)
    depth, current, parts = 0, [], []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())

    table_constraint_re = re.compile(
        r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b", re.IGNORECASE
    )

    columns: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part or table_constraint_re.match(part):
            continue  # skip table-level constraints

        tokens = part.split()
        col_name = tokens[0].lower()
        col_type = tokens[1] if len(tokens) > 1 else "TEXT"

        # Preserve the DEFAULT clause (including parenthesised expressions)
        default_match = re.search(r"\bDEFAULT\s+", part, re.IGNORECASE)
        default_clause = ""
        if default_match:
            start = default_match.end()
            if start < len(part) and part[start] == "(":
                # Walk forward to find the matching closing parenthesis
                depth2, i = 0, start
                while i < len(part):
                    if part[i] == "(":
                        depth2 += 1
                    elif part[i] == ")":
                        depth2 -= 1
                        if depth2 == 0:
                            default_clause = "DEFAULT " + part[start : i + 1]
                            break
                    i += 1
            else:
                # Simple scalar default (number, string literal, keyword)
                token_match = re.match(r"('[^']*'|\S+)", part[start:])
                if token_match:
                    default_clause = "DEFAULT " + token_match.group(1)

        alter_def = f"{col_name} {col_type}"
        if default_clause:
            alter_def += f" {default_clause}"

        columns.append((col_name, alter_def))

    return columns


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def op_adapt(db_path: Path, sql: str) -> None:
    """
    Apply schema changes to an existing database without data loss:
      - Creates new tables present in the schema but missing from the DB.
      - Adds missing columns to existing tables via ALTER TABLE ADD COLUMN.
      - Tables absent from the schema are left completely untouched.

    A timestamped backup is created before any changes are made.
    """
    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        sys.exit(1)

    make_backup(db_path)

    schema_tables = parse_create_tables(sql)
    added_tables: list[str] = []
    added_columns: list[str] = []

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")

            # Collect table names that already exist in the database
            existing_tables = {
                row[0].lower()
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }

            for table_name, create_sql in schema_tables.items():

                if table_name not in existing_tables:
                    # New table — ensure IF NOT EXISTS so the statement is idempotent
                    safe_sql = re.sub(
                        r"CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\s+)(\w+)",
                        r"CREATE TABLE IF NOT EXISTS \1",
                        create_sql,
                        flags=re.IGNORECASE,
                    )
                    if not safe_sql.rstrip().endswith(";"):
                        safe_sql += ";"
                    conn.executescript(safe_sql)
                    added_tables.append(table_name)
                    print(f"  [CREATED] table '{table_name}'")

                else:
                    # Existing table — add any columns that are missing
                    existing_columns = {
                        row[1].lower()
                        for row in conn.execute(f"PRAGMA table_info('{table_name}')")
                    }
                    for col_name, alter_def in parse_columns(create_sql):
                        if col_name not in existing_columns:
                            conn.execute(
                                f"ALTER TABLE {table_name} ADD COLUMN {alter_def}"
                            )
                            added_columns.append(f"{table_name}.{col_name}")
                            print(f"  [ADDED]   column '{col_name}' to table '{table_name}'")

            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

    except sqlite3.Error as exc:
        print(f"[ERROR] SQLite: {exc}")
        sys.exit(1)

    # Summary
    if not added_tables and not added_columns:
        print("[OK] Database is already up to date — no changes were needed.")
    else:
        print(f"[OK] Database adapted: {db_path.resolve()}")
    list_objects(db_path)
