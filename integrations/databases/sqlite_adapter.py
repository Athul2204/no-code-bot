"""SQLite adapter: read-only by default, parameterized SQL, identifiers whitelisted from the schema."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import DatabaseAdapter, DatabaseError

FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,100}\.(db|sqlite|sqlite3)$")
OPS = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
MAX_ROWS = 50
MAX_CELL = 500


def resolve_db_file(base_dir: str, filename: str) -> Path:
    """Map a bare file name to a path inside the user database directory (no traversal)."""
    if not isinstance(filename, str) or not FILENAME_RE.match(filename):
        raise DatabaseError("Choose a database file ending in .db, .sqlite or .sqlite3.")
    base = Path(base_dir).resolve()
    path = (base / filename).resolve()
    if base not in path.parents:
        raise DatabaseError("That database file is outside the allowed folder.")
    return path


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _clean_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…"
    return value


class SQLiteAdapter(DatabaseAdapter):
    db_type = "sqlite"

    def __init__(self, path: Path, allow_writes: bool = False):
        self.path = Path(path)
        self.allow_writes = allow_writes

    @classmethod
    def from_config(cls, config: dict, user_db_dir: str, allow_writes: bool = False) -> "SQLiteAdapter":
        return cls(resolve_db_file(user_db_dir, config.get("file", "")), allow_writes)

    # -- connection ------------------------------------------------------------------
    def _connect(self, write: bool = False) -> sqlite3.Connection:
        if write and not self.allow_writes:
            raise DatabaseError("This connection is read-only. Enable writes on the Connect Database page first.")
        if not self.path.exists():
            raise DatabaseError("The database file was not found.")
        mode = "rw" if write else "ro"
        try:
            conn = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=5)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not open the database: {exc}") from exc
        conn.row_factory = sqlite3.Row
        if not write:
            conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _open(self, write: bool = False):
        conn = self._connect(write)
        try:
            yield conn
        finally:
            conn.close()

    # -- introspection ---------------------------------------------------------------
    def test(self) -> dict:
        try:
            with self._open() as conn:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {"ok": True, "message": f"Connected. Found {len(tables)} table(s).", "table_count": len(tables)}
        except sqlite3.DatabaseError as exc:
            return {"ok": False, "message": f"Not a valid SQLite database: {exc}"}
        except DatabaseError as exc:
            return {"ok": False, "message": str(exc)}

    def list_tables(self) -> list[str]:
        try:
            with self._open() as conn:
                return [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not read tables: {exc}") from exc

    def describe(self, table: str) -> list[dict]:
        self._require_table(table)
        with self._open() as conn:
            rows = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        return [{"name": r["name"], "type": r["type"] or "TEXT", "notnull": bool(r["notnull"]),
                 "pk": bool(r["pk"]), "default": r["dflt_value"]} for r in rows]

    def _require_table(self, table: str) -> None:
        if table not in self.list_tables():
            raise DatabaseError(f"Table '{table}' does not exist in this database.")

    def _require_columns(self, table: str, columns: list[str]) -> list[dict]:
        meta = self.describe(table)
        known = {c["name"] for c in meta}
        for col in columns:
            if col not in known:
                raise DatabaseError(f"Column '{col}' does not exist in table '{table}'.")
        return meta

    # -- reads -----------------------------------------------------------------------
    def select(self, table, columns, filters, limit=20):
        meta = self._require_columns(table, (columns or []) + [f["column"] for f in filters])
        cols = columns or [c["name"] for c in meta]
        limit = max(1, min(int(limit), MAX_ROWS))
        where, params = [], []
        for f in filters:
            col, op, val = _quote(f["column"]), f.get("op", "eq"), f["value"]
            if op == "contains":
                escaped = str(val).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                where.append(f"{col} LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")
            elif op in OPS:
                if op == "eq" and isinstance(val, str):
                    where.append(f"{col} = ? COLLATE NOCASE")
                else:
                    where.append(f"{col} {OPS[op]} ?")
                params.append(val)
            else:
                raise DatabaseError(f"Unsupported filter operator '{op}'.")
        sql = f"SELECT {', '.join(_quote(c) for c in cols)} FROM {_quote(table)}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " LIMIT ?"
        params.append(limit)
        try:
            with self._open() as conn:
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Query failed: {exc}") from exc
        return [{k: _clean_cell(r[k]) for k in r.keys()} for r in rows]

    def get_by_id(self, table, id_column, value, columns):
        rows = self.select(table, columns, [{"column": id_column, "op": "eq", "value": value}], 1)
        return rows[0] if rows else None

    # -- writes ----------------------------------------------------------------------
    def _check_insert(self, table: str, values: dict) -> None:
        meta = self._require_columns(table, list(values))
        if not values:
            raise DatabaseError("Nothing to insert.")
        for col in meta:
            if col["notnull"] and col["default"] is None and not col["pk"] and col["name"] not in values:
                raise DatabaseError(f"Column '{col['name']}' is required by the table but no value was supplied.")

    def insert(self, table, values, dry_run=False):
        self._check_insert(table, values)
        if dry_run:
            return {"simulated": True, "would_insert": values}
        cols = list(values)
        sql = f"INSERT INTO {_quote(table)} ({', '.join(_quote(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})"
        try:
            with self._open(write=True) as conn:
                cur = conn.execute(sql, [values[c] for c in cols])
                conn.commit()
                return {"inserted_id": cur.lastrowid, "rows_affected": cur.rowcount}
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(f"The database rejected this record: {exc}") from exc
        except sqlite3.Error as exc:
            raise DatabaseError(f"Insert failed: {exc}") from exc

    def update(self, table, id_column, id_value, values, dry_run=False):
        self._require_columns(table, list(values) + [id_column])
        if not values:
            raise DatabaseError("Nothing to update.")
        if dry_run:
            exists = self.get_by_id(table, id_column, id_value, [id_column]) is not None
            return {"simulated": True, "record_found": exists, "would_update": values}
        sets = ", ".join(f"{_quote(c)} = ?" for c in values)
        sql = f"UPDATE {_quote(table)} SET {sets} WHERE {_quote(id_column)} = ?"
        try:
            with self._open(write=True) as conn:
                cur = conn.execute(sql, [*values.values(), id_value])
                conn.commit()
                return {"rows_affected": cur.rowcount}
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(f"The database rejected this update: {exc}") from exc
        except sqlite3.Error as exc:
            raise DatabaseError(f"Update failed: {exc}") from exc
