# SQLiteDB

A small toolkit for creating and managing SQLite databases from SQL schemas and CSV files.

## Scripts

### `create_db.py` — Create or update a database from a schema

```
python create_db.py                          # uses schema.sql + database.db (defaults)
python create_db.py --schema my.sql          # custom schema file
python create_db.py --db my.db               # custom database file
python create_db.py --schema s.sql --db d.db
```

If the database file already exists, the script prompts:

| Choice | Behaviour |
|--------|-----------|
| **1 Replace** | Backs up current DB, deletes it, creates a fresh one from schema |
| **2 Adapt**   | Backs up current DB, adds missing tables/columns from schema (existing data kept) |
| **3 Cancel**  | Aborts with no changes |

---

### `migrate_db.py` — Apply schema migrations to an existing database

Standalone script for the "adapt" operation (adds missing tables/columns without touching existing data). Use this for recurring migrations without the interactive prompt.

---

### `csv_to_schema.py` — Generate a schema from CSV files

Reads a sample of rows, infers column types (`INTEGER`, `REAL`, `TEXT`), detects a likely primary key, and emits `CREATE TABLE IF NOT EXISTS` statements.

```
python csv_to_schema.py data.csv                        # print schema to stdout
python csv_to_schema.py users.csv orders.csv            # one table per file
python csv_to_schema.py data.csv --output schema.sql    # save to file
python csv_to_schema.py report.csv --table my_table     # override table name
python csv_to_schema.py data.csv --sample 2000          # inspect more rows (default: 500)
python csv_to_schema.py data.csv --dry-run              # show inferred types only
```

---

### `import_csv.py` — Import CSV files into a database

Maps CSV columns to table columns by name (case-insensitive), skips unmatched columns, and inserts rows in batches. Always backs up the database before writing.

```
python import_csv.py users.csv --db database.db
python import_csv.py report.csv --db database.db --table users
python import_csv.py users.csv orders.csv --db database.db
python import_csv.py users.csv --db database.db --on-conflict replace
python import_csv.py users.csv --db database.db --on-conflict fail
python import_csv.py users.csv --db database.db --dry-run
python import_csv.py big.csv --db database.db --batch 2000
```

Conflict strategies:

| `--on-conflict` | Behaviour |
|-----------------|-----------|
| `skip` (default) | Silently ignore conflicting rows |
| `replace` | Overwrite existing rows |
| `fail` | Abort on first conflict |

---

### `db_utils.py` — Shared utilities

Internal helpers used by the other scripts: schema reading, database backups, object listing, and the adapt (migration) logic.

## Typical workflow

```bash
# 1. Generate a schema from your CSVs
python csv_to_schema.py users.csv orders.csv --output schema.sql

# 2. Create the database
python create_db.py --schema schema.sql --db database.db

# 3. Import data
python import_csv.py users.csv orders.csv --db database.db
```
