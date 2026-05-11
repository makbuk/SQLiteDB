"""
compare_schema.py — compare two SQL schema files and report differences.

Compares:
  - New tables      (present in A, missing in B)
  - Removed tables  (present in B, missing in A)
  - Changed columns (type, position) for tables present in both
  - Added / removed indexes
  - Added / removed views

Usage:
    python compare_schema.py schema_v1.sql schema_v2.sql
    python compare_schema.py schema_v1.sql schema_v2.sql --diff-only
    python compare_schema.py schema_v1.sql schema_v2.sql --output diff.txt

Exit code: 0 — schemas identical, 1 — differences found.
"""

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    name:    str
    type:    str        # e.g. "INTEGER", "TEXT", "REAL"
    notnull: bool
    default: str | None
    pk:      bool
    order:   int        # 0-based position in table


@dataclass
class IndexInfo:
    name:    str
    unique:  bool
    columns: list[str]


@dataclass
class TableSchema:
    name:    str
    columns: dict[str, ColumnInfo]   # keyed by column name (lowercase)
    indexes: dict[str, IndexInfo]    # keyed by index name (lowercase)


@dataclass
class SchemaSnapshot:
    source: str                       # file path, shown in report header
    tables: dict[str, TableSchema]    # keyed by table name (lowercase)
    views:  dict[str, str]            # keyed by view name (lowercase) → sql


# ---------------------------------------------------------------------------
# Loading: parse .sql into an in-memory SQLite DB, then introspect
# ---------------------------------------------------------------------------

def _read_sql_file(path: Path) -> str:
    """Read and validate a .sql schema file."""
    if not path.exists():
        print(f"[ERROR] File not found: {path}")
        sys.exit(1)
    if path.suffix.lower() not in (".sql",):
        print(f"[ERROR] Expected a .sql file, got: {path}")
        sys.exit(1)
    sql = path.read_text(encoding="utf-8").strip()
    if not sql:
        print(f"[ERROR] File is empty: {path}")
        sys.exit(1)
    return sql


def _snapshot_from_sql(path: Path) -> SchemaSnapshot:
    """
    Execute the SQL schema in a temporary in-memory database,
    then introspect it with PRAGMA to build a SchemaSnapshot.
    """
    sql = _read_sql_file(path)
    try:
        conn = sqlite3.connect(":memory:")
        conn.executescript(sql)
    except sqlite3.Error as exc:
        print(f"[ERROR] Cannot parse {path.name}: {exc}")
        sys.exit(1)

    tables: dict[str, TableSchema] = {}
    views:  dict[str, str]         = {}

    # Views
    for row in conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='view' AND name NOT LIKE 'sqlite_%'"
    ):
        views[row[0].lower()] = (row[1] or "").strip()

    # Tables
    for (tname,) in conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        # Columns
        columns: dict[str, ColumnInfo] = {}
        for cid, cname, ctype, notnull, dflt, pk in conn.execute(
            f"PRAGMA table_info('{tname}')"
        ):
            columns[cname.lower()] = ColumnInfo(
                name    = cname,
                type    = (ctype or "TEXT").upper().strip(),
                notnull = bool(notnull),
                default = dflt,
                pk      = bool(pk),
                order   = cid,
            )

        # Indexes (user-created only)
        indexes: dict[str, IndexInfo] = {}
        for idx_row in conn.execute(f"PRAGMA index_list('{tname}')"):
            iname, iunique, iorigin = idx_row[1], bool(idx_row[2]), idx_row[3]
            if iorigin not in ("c", "u"):
                continue  # skip auto PK indexes
            icols = [r[2] for r in conn.execute(f"PRAGMA index_info('{iname}')")]
            indexes[iname.lower()] = IndexInfo(
                name    = iname,
                unique  = iunique,
                columns = icols,
            )

        tables[tname.lower()] = TableSchema(
            name    = tname,
            columns = columns,
            indexes = indexes,
        )

    conn.close()
    return SchemaSnapshot(source=str(path), tables=tables, views=views)


# ---------------------------------------------------------------------------
# Diff symbols
# ---------------------------------------------------------------------------

ADDED   = "ADDED"
REMOVED = "REMOVED"
CHANGED = "CHANGED"
OK      = "OK"

_SYM = {ADDED: "+", REMOVED: "-", CHANGED: "~", OK: " "}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class DiffReport:
    """Accumulates diff lines and renders them as a text report."""

    def __init__(self, a_label: str, b_label: str, diff_only: bool) -> None:
        self.a_label   = a_label
        self.b_label   = b_label
        self.diff_only = diff_only
        self.lines:    list[str] = []
        self.has_diff: bool      = False

        # Counters
        self.counts: dict[str, int] = {
            "tables added":    0,
            "tables removed":  0,
            "columns added":   0,
            "columns removed": 0,
            "columns changed": 0,
            "indexes added":   0,
            "indexes removed": 0,
            "views added":     0,
            "views removed":   0,
        }

    # -- output primitives ---------------------------------------------------

    def _line(self, text: str = "") -> None:
        self.lines.append(text)

    def _section(self, title: str) -> None:
        self._line("─" * 70)
        self._line(f"  {title}")
        self._line("─" * 70)

    def _row(self, status: str, text: str, indent: int = 0) -> None:
        if status != OK:
            self.has_diff = True
        if self.diff_only and status == OK:
            return
        self._line(f"  {_SYM[status]} {'  ' * indent}{text}")

    # -- high-level builders -------------------------------------------------

    def header(self) -> None:
        self._line("=" * 70)
        self._line("SCHEMA DIFF REPORT")
        self._line("=" * 70)
        self._line(f"  A : {self.a_label}")
        self._line(f"  B : {self.b_label}")
        self._line("=" * 70)
        self._line("Legend:  + added in B   - removed in B   ~ changed   (space) identical")
        self._line()

    def table_added(self, name: str, col_count: int) -> None:
        self._row(ADDED, f"TABLE  {name}  ({col_count} columns)")
        self.counts["tables added"] += 1

    def table_removed(self, name: str, col_count: int) -> None:
        self._row(REMOVED, f"TABLE  {name}  ({col_count} columns)")
        self.counts["tables removed"] += 1

    def table_header(self, name: str, has_changes: bool) -> None:
        self._row(CHANGED if has_changes else OK, f"TABLE  {name}")

    def col_added(self, col: ColumnInfo) -> None:
        self._row(ADDED, f"column  {col.name}  [{col.type}]", indent=1)
        self.counts["columns added"] += 1

    def col_removed(self, col: ColumnInfo) -> None:
        self._row(REMOVED, f"column  {col.name}  [{col.type}]", indent=1)
        self.counts["columns removed"] += 1

    def col_changed(self, name: str, diffs: list[str]) -> None:
        self._row(CHANGED, f"column  {name}  ({', '.join(diffs)})", indent=1)
        self.counts["columns changed"] += 1

    def col_ok(self, col: ColumnInfo) -> None:
        self._row(OK, f"column  {col.name}  [{col.type}]", indent=1)

    def index_added(self, idx: IndexInfo) -> None:
        cols = ", ".join(idx.columns)
        uniq = " UNIQUE" if idx.unique else ""
        self._row(ADDED, f"index   {idx.name}{uniq}  ({cols})", indent=1)
        self.counts["indexes added"] += 1

    def index_removed(self, idx: IndexInfo) -> None:
        cols = ", ".join(idx.columns)
        self._row(REMOVED, f"index   {idx.name}  ({cols})", indent=1)
        self.counts["indexes removed"] += 1

    def index_ok(self, idx: IndexInfo) -> None:
        self._row(OK, f"index   {idx.name}", indent=1)

    def gap(self) -> None:
        self._line()

    def section(self, title: str) -> None:
        self._section(title)

    def view_added(self, name: str) -> None:
        self._row(ADDED, f"VIEW  {name}")
        self.counts["views added"] += 1

    def view_removed(self, name: str) -> None:
        self._row(REMOVED, f"VIEW  {name}")
        self.counts["views removed"] += 1

    def view_ok(self, name: str) -> None:
        self._row(OK, f"VIEW  {name}")

    def summary(self) -> None:
        self._line()
        self._line("=" * 70)
        if not self.has_diff:
            self._line("  RESULT: schemas are identical.")
        else:
            self._line("  RESULT: differences found.")
            for label, n in self.counts.items():
                if n:
                    self._line(f"    {label}: {n}")
        self._line("=" * 70)

    def render(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def _norm_view(sql: str) -> str:
    """Normalise a VIEW definition for comparison (collapse whitespace)."""
    return re.sub(r"\s+", " ", sql).strip().lower()


def compare(a: SchemaSnapshot, b: SchemaSnapshot, diff_only: bool) -> DiffReport:
    report = DiffReport(a.source, b.source, diff_only)
    report.header()

    all_tables = sorted(a.tables.keys() | b.tables.keys())

    # ---------------------------------------------------------------- TABLES
    report.section("TABLES")
    report.gap()

    for tname in all_tables:
        in_a = tname in a.tables
        in_b = tname in b.tables

        if in_a and not in_b:
            report.table_removed(tname, len(a.tables[tname].columns))
            report.gap()
            continue

        if not in_a and in_b:
            report.table_added(tname, len(b.tables[tname].columns))
            report.gap()
            continue

        # Present in both — compare columns and indexes
        ta, tb = a.tables[tname], b.tables[tname]

        all_cols = sorted(
            ta.columns.keys() | tb.columns.keys(),
            key=lambda c: ta.columns[c].order if c in ta.columns else tb.columns[c].order,
        )

        # Collect per-column verdicts first, to decide the table header status
        verdicts: list[tuple[str, ColumnInfo | None, ColumnInfo | None, list[str]]] = []
        for cname in all_cols:
            ca = ta.columns.get(cname)
            cb = tb.columns.get(cname)
            if ca and not cb:
                verdicts.append((REMOVED, ca, None, []))
            elif not ca and cb:
                verdicts.append((ADDED, None, cb, []))
            else:
                diffs: list[str] = []
                if ca.type != cb.type:
                    diffs.append(f"type: {ca.type} → {cb.type}")
                if ca.order != cb.order:
                    diffs.append(f"position: {ca.order} → {cb.order}")
                if ca.notnull != cb.notnull:
                    diffs.append(f"notnull: {ca.notnull} → {cb.notnull}")
                if ca.default != cb.default:
                    diffs.append(f"default: {ca.default!r} → {cb.default!r}")
                verdicts.append((CHANGED if diffs else OK, ca, cb, diffs))

        # Index verdicts
        all_idx = sorted(ta.indexes.keys() | tb.indexes.keys())
        idx_verdicts: list[tuple[str, IndexInfo | None, IndexInfo | None]] = []
        for iname in all_idx:
            ia = ta.indexes.get(iname)
            ib = tb.indexes.get(iname)
            if ia and not ib:
                idx_verdicts.append((REMOVED, ia, None))
            elif not ia and ib:
                idx_verdicts.append((ADDED, None, ib))
            else:
                idx_verdicts.append((OK, ia, ib))

        has_changes = (
            any(s != OK for s, *_ in verdicts) or
            any(s != OK for s, *_ in idx_verdicts)
        )
        report.table_header(tname, has_changes)

        # Emit column rows
        for status, ca, cb, diffs in verdicts:
            if status == REMOVED:
                report.col_removed(ca)
            elif status == ADDED:
                report.col_added(cb)
            elif status == CHANGED:
                report.col_changed(ca.name, diffs)
            else:
                report.col_ok(ca)

        # Emit index rows
        for status, ia, ib in idx_verdicts:
            if status == REMOVED:
                report.index_removed(ia)
            elif status == ADDED:
                report.index_added(ib)
            else:
                report.index_ok(ia)

        report.gap()

    # ----------------------------------------------------------------- VIEWS
    all_views = sorted(a.views.keys() | b.views.keys())
    if all_views:
        report.section("VIEWS")
        report.gap()
        for vname in all_views:
            va = a.views.get(vname)
            vb = b.views.get(vname)
            if va and not vb:
                report.view_removed(vname)
            elif not va and vb:
                report.view_added(vname)
            else:
                # Views are present in both — we only report added/removed,
                # so treat changed definition as informational (not in scope)
                report.view_ok(vname)
        report.gap()

    report.summary()
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two SQL schema files and report differences"
    )
    parser.add_argument("schema_a", metavar="A.sql",
                        help="First schema file")
    parser.add_argument("schema_b", metavar="B.sql",
                        help="Second schema file")
    parser.add_argument("--diff-only", action="store_true",
                        help="Show only objects with differences (hide identical)")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Save the report to this file")
    args = parser.parse_args()

    snap_a = _snapshot_from_sql(Path(args.schema_a))
    snap_b = _snapshot_from_sql(Path(args.schema_b))

    report = compare(snap_a, snap_b, diff_only=args.diff_only)
    text   = report.render()

    print(text)

    if args.output:
        out = Path(args.output)
        out.write_text(text, encoding="utf-8")
        print(f"\n[OK] Report saved to: {out.resolve()}")

    sys.exit(1 if report.has_diff else 0)


if __name__ == "__main__":
    main()
