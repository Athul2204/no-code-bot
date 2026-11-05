"""Chatbot CRUD with versioning."""
from __future__ import annotations

from ..db import dumps, execute, execute_rowcount, get_db, loads, q_all, q_one
from ..validation import ValidationError, validate_chatbot_payload

FIELDS = ("name", "description", "purpose", "welcome_message", "system_instructions")


def _view(row: dict, settings: dict | None) -> dict:
    s = settings or {}
    return {
        **{k: row[k] for k in ("id", "name", "description", "purpose", "welcome_message", "system_instructions", "version", "created_at", "updated_at")},
        "enabled_actions": loads(s.get("enabled_actions"), []),
        "temperature": s.get("temperature", 0.2), "model_override": s.get("model_override", ""),
        "test_mode_default": bool(s.get("test_mode_default", 1)),
    }


def get_bot(user_id: int, bot_id: int) -> dict | None:
    row = q_one("SELECT * FROM chatbots WHERE id = ? AND user_id = ?", (bot_id, user_id))
    if not row:
        return None
    return _view(row, q_one("SELECT * FROM chatbot_settings WHERE chatbot_id = ?", (bot_id,)))


def list_bots(user_id: int) -> list[dict]:
    rows = q_all(
        "SELECT c.*, (SELECT count(*) FROM conversations v WHERE v.chatbot_id = c.id) AS conversation_count "
        "FROM chatbots c WHERE c.user_id = ? ORDER BY c.updated_at DESC, c.id DESC", (user_id,))
    out = []
    for r in rows:
        v = _view(r, q_one("SELECT * FROM chatbot_settings WHERE chatbot_id = ?", (r["id"],)))
        v["conversation_count"] = r["conversation_count"]
        out.append(v)
    return out


def _check_actions(user_id: int, ids: list[int]) -> None:
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    found = {r["id"] for r in q_all(f"SELECT id FROM custom_actions WHERE user_id = ? AND id IN ({marks})", [user_id, *ids])}
    if set(ids) - found:
        raise ValidationError({"enabled_actions": "One of the selected actions does not exist."})


def _snapshot(bot_id: int, version: int) -> None:
    bot = q_one("SELECT * FROM chatbots WHERE id = ?", (bot_id,))
    settings = q_one("SELECT * FROM chatbot_settings WHERE chatbot_id = ?", (bot_id,))
    snap = {**{k: bot[k] for k in FIELDS}, "enabled_actions": loads(settings["enabled_actions"], []),
            "temperature": settings["temperature"], "model_override": settings["model_override"],
            "test_mode_default": bool(settings["test_mode_default"])}
    execute("INSERT INTO chatbot_versions (chatbot_id, version_number, snapshot) VALUES (?,?,?)", (bot_id, version, dumps(snap)))


def create_bot(user_id: int, data: dict) -> dict:
    p = validate_chatbot_payload(data)
    _check_actions(user_id, p["enabled_actions"])
    bot_id = execute(
        "INSERT INTO chatbots (user_id, name, description, purpose, welcome_message, system_instructions) VALUES (?,?,?,?,?,?)",
        (user_id, p["name"], p["description"], p["purpose"], p["welcome_message"], p["system_instructions"]))
    execute("INSERT INTO chatbot_settings (chatbot_id, enabled_actions, temperature, model_override, test_mode_default) VALUES (?,?,?,?,?)",
            (bot_id, dumps(p["enabled_actions"]), p["temperature"], p["model_override"], p["test_mode_default"]))
    _snapshot(bot_id, 1)
    return get_bot(user_id, bot_id)


def _apply(bot_id: int, p: dict, version: int) -> None:
    execute_rowcount(
        "UPDATE chatbots SET name=?, description=?, purpose=?, welcome_message=?, system_instructions=?, version=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (p["name"], p["description"], p["purpose"], p["welcome_message"], p["system_instructions"], version, bot_id))
    execute_rowcount("UPDATE chatbot_settings SET enabled_actions=?, temperature=?, model_override=?, test_mode_default=? WHERE chatbot_id=?",
                     (dumps(p["enabled_actions"]), p["temperature"], p["model_override"], p["test_mode_default"], bot_id))
    _snapshot(bot_id, version)


def update_bot(user_id: int, bot_id: int, data: dict) -> dict | None:
    bot = get_bot(user_id, bot_id)
    if not bot:
        return None
    p = validate_chatbot_payload(data)
    _check_actions(user_id, p["enabled_actions"])
    _apply(bot_id, p, bot["version"] + 1)
    return get_bot(user_id, bot_id)


def duplicate_bot(user_id: int, bot_id: int) -> dict | None:
    bot = get_bot(user_id, bot_id)
    if not bot:
        return None
    payload = {**bot, "name": f"{bot['name']} (copy)"[:80]}
    return create_bot(user_id, payload)


def delete_bot(user_id: int, bot_id: int) -> bool:
    return execute_rowcount("DELETE FROM chatbots WHERE id = ? AND user_id = ?", (bot_id, user_id)) > 0


def list_versions(user_id: int, bot_id: int) -> list[dict] | None:
    if not get_bot(user_id, bot_id):
        return None
    rows = q_all("SELECT id, version_number, snapshot, created_at FROM chatbot_versions WHERE chatbot_id = ? ORDER BY version_number DESC", (bot_id,))
    return [{"id": r["id"], "version_number": r["version_number"], "created_at": r["created_at"],
             "name": loads(r["snapshot"], {}).get("name", ""), "action_count": len(loads(r["snapshot"], {}).get("enabled_actions", []))} for r in rows]


def restore_version(user_id: int, bot_id: int, version_id: int) -> dict | None:
    bot = get_bot(user_id, bot_id)
    row = q_one("SELECT snapshot FROM chatbot_versions WHERE id = ? AND chatbot_id = ?", (version_id, bot_id))
    if not bot or not row:
        return None
    snap = loads(row["snapshot"], {})
    # drop actions that have been deleted since
    existing = {r["id"] for r in q_all("SELECT id FROM custom_actions WHERE user_id = ?", (user_id,))}
    snap["enabled_actions"] = [i for i in snap.get("enabled_actions", []) if i in existing]
    p = validate_chatbot_payload(snap)
    _apply(bot_id, p, bot["version"] + 1)
    return get_bot(user_id, bot_id)
