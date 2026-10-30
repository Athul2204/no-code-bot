"""SQLite access for the application database (parameterized SQL only)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from flask import current_app, g

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def q_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in get_db().execute(sql, tuple(params)).fetchall()]


def q_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    row = get_db().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a write statement, commit, and return lastrowid."""
    db = get_db()
    cur = db.execute(sql, tuple(params))
    db.commit()
    return cur.lastrowid


def execute_rowcount(sql: str, params: Iterable[Any] = ()) -> int:
    db = get_db()
    cur = db.execute(sql, tuple(params))
    db.commit()
    return cur.rowcount


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default
