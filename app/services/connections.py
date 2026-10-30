"""Database connection management. Credentials are encrypted at rest and never returned to clients."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app

from ..db import dumps, execute, execute_rowcount, loads, q_all, q_one
from ..integrations.databases import DatabaseError, build_adapter, engine_choices
from ..integrations.databases.sqlite_adapter import FILENAME_RE
from ..security import decrypt_text, encrypt_text
from ..validation import ValidationError, clean_text

SQLITE_MAGIC = b"SQLite format 3\x00"


def public_view(row: dict) -> dict:
    cfg = loads(decrypt_text(row["config_encrypted"]), {})
    safe_cfg = {k: v for k, v in cfg.items() if k in ("file",)}  # whitelist: no secrets leave the server
    return {
        "id": row["id"], "name": row["name"], "db_type": row["db_type"],
        "allow_writes": bool(row["allow_writes"]), "config": safe_cfg,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_connections(user_id: int) -> list[dict]:
    rows = q_all("SELECT * FROM database_connections WHERE user_id = ? ORDER BY name", (user_id,))
    return [public_view(r) for r in rows]


def get_connection_row(user_id: int, conn_id: int) -> dict | None:
    return q_one("SELECT * FROM database_connections WHERE id = ? AND user_id = ?", (conn_id, user_id))


def adapter_for(row: dict):
    cfg = loads(decrypt_text(row["config_encrypted"]), {})
    return build_adapter(row["db_type"], cfg, current_app.config["USER_DB_DIR"], bool(row["allow_writes"]))


def validate_payload(data: dict) -> tuple[str, str, dict]:
    errors: dict[str, str] = {}
    name = clean_text(data.get("name"), "name", errors, min_len=2, max_len=80, required=True)
    db_type = clean_text(data.get("db_type") or "sqlite", "db_type", errors, max_len=20)
    engines = {e["value"]: e for e in engine_choices()}
    if db_type not in engines:
        errors["db_type"] = "Unknown database type."
    elif not engines[db_type]["available"]:
        errors["db_type"] = f"{engines[db_type]['label']} support is coming soon. Please use SQLite for now."
    config = data.get("config") or {}
    if db_type == "sqlite":
        filename = config.get("file") if isinstance(config, dict) else None
        if not isinstance(filename, str) or not FILENAME_RE.match(filename):
            errors["file"] = "Choose a database file."
        else:
            config = {"file": filename}
    if errors:
        raise ValidationError(errors)
    return name, db_type, config


def create_connection(user_id: int, data: dict) -> dict:
    name, db_type, config = validate_payload(data)
    if q_one("SELECT 1 FROM database_connections WHERE user_id = ? AND name = ?", (user_id, name)):
        raise ValidationError({"name": "You already have a connection with this name."})
    # Test before saving so users find problems immediately.
    adapter = build_adapter(db_type, config, current_app.config["USER_DB_DIR"], False)
    result = adapter.test()
    if not result["ok"]:
        raise ValidationError({"file": result["message"]})
    new_id = execute(
        "INSERT INTO database_connections (user_id, name, db_type, config_encrypted) VALUES (?,?,?,?)",
        (user_id, name, db_type, encrypt_text(dumps(config))),
    )
    return public_view(get_connection_row(user_id, new_id))


def update_connection(user_id: int, conn_id: int, data: dict) -> dict | None:
    row = get_connection_row(user_id, conn_id)
    if not row:
        return None
    name, db_type, config = validate_payload(data)
    clash = q_one("SELECT id FROM database_connections WHERE user_id = ? AND name = ? AND id != ?", (user_id, name, conn_id))
    if clash:
        raise ValidationError({"name": "You already have a connection with this name."})
    result = build_adapter(db_type, config, current_app.config["USER_DB_DIR"], False).test()
    if not result["ok"]:
        raise ValidationError({"file": result["message"]})
    execute_rowcount(
        "UPDATE database_connections SET name=?, db_type=?, config_encrypted=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (name, db_type, encrypt_text(dumps(config)), conn_id),
    )
    return public_view(get_connection_row(user_id, conn_id))


def delete_connection(user_id: int, conn_id: int) -> bool:
    return execute_rowcount("DELETE FROM database_connections WHERE id = ? AND user_id = ?", (conn_id, user_id)) > 0


def set_allow_writes(user_id: int, conn_id: int, allow: bool) -> dict | None:
    if not get_connection_row(user_id, conn_id):
        return None
    execute_rowcount(
        "UPDATE database_connections SET allow_writes=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (1 if allow else 0, conn_id),
    )
    return public_view(get_connection_row(user_id, conn_id))


def schema_for(row: dict) -> list[dict]:
    adapter = adapter_for(row)
    return [{"name": t, "columns": adapter.describe(t)} for t in adapter.list_tables()]


def available_files() -> list[str]:
    base = Path(current_app.config["USER_DB_DIR"])
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_file() and FILENAME_RE.match(p.name))


def save_uploaded_database(file_storage) -> str:
    """Store an uploaded SQLite file in the user database folder after validating its header."""
    from werkzeug.utils import secure_filename

    filename = secure_filename(file_storage.filename or "")
    if not FILENAME_RE.match(filename):
        raise ValidationError({"file": "File name must end in .db, .sqlite or .sqlite3."})
    data = file_storage.read()
    if not data.startswith(SQLITE_MAGIC):
        raise ValidationError({"file": "That file is not a SQLite database."})
    target = Path(current_app.config["USER_DB_DIR"]) / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 2
        while (target.parent / f"{stem}_{i}{suffix}").exists():
            i += 1
        target = target.parent / f"{stem}_{i}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:  # confirm it opens
        c = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
        c.execute("SELECT count(*) FROM sqlite_master").fetchone()
        c.close()
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise ValidationError({"file": f"Could not open that database: {exc}"}) from exc
    return target.name
