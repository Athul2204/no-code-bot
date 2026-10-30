"""Database engine registry. Add MySQL/PostgreSQL by implementing DatabaseAdapter and registering it here."""
from __future__ import annotations

from .base import DatabaseAdapter, DatabaseError
from .sqlite_adapter import SQLiteAdapter, resolve_db_file

ENGINES = {
    "sqlite": {"label": "SQLite", "available": True, "adapter": SQLiteAdapter},
    "mysql": {"label": "MySQL", "available": False, "adapter": None},
    "postgresql": {"label": "PostgreSQL", "available": False, "adapter": None},
}


def engine_choices() -> list[dict]:
    return [{"value": k, "label": v["label"], "available": v["available"]} for k, v in ENGINES.items()]


def build_adapter(db_type: str, config: dict, user_db_dir: str, allow_writes: bool) -> DatabaseAdapter:
    engine = ENGINES.get(db_type)
    if not engine or not engine["available"]:
        raise DatabaseError(f"{db_type} connections are not supported yet.")
    return engine["adapter"].from_config(config, user_db_dir, allow_writes)


__all__ = ["DatabaseAdapter", "DatabaseError", "SQLiteAdapter", "build_adapter", "engine_choices", "resolve_db_file"]
