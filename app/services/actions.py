"""Custom action CRUD, approval workflow and LLM tool-schema generation."""
from __future__ import annotations

from typing import Any

from ..actions.templates import TEMPLATES, is_write_action, validate_action_config
from ..db import dumps, execute, execute_rowcount, get_db, loads, q_all, q_one
from ..security import decrypt_text, encrypt_text
from ..validation import ValidationError, TOOL_NAME_RE, clean_text, slugify
from . import connections as conn_svc


def _params_for(action_ids: list[int]) -> dict[int, list[dict]]:
    if not action_ids:
        return {}
    marks = ",".join("?" for _ in action_ids)
    rows = q_all(f"SELECT * FROM action_parameters WHERE action_id IN ({marks}) ORDER BY position, id", action_ids)
    out: dict[int, list[dict]] = {i: [] for i in action_ids}
    for r in rows:
        r["required"] = bool(r["required"])
        r["extra"] = loads(r["extra"], {})
        out[r["action_id"]].append(r)
    return out


def _hydrate(rows: list[dict]) -> list[dict]:
    params = _params_for([r["id"] for r in rows])
    for r in rows:
        r["config"] = loads(r["config"], {})
        r["parameters"] = params.get(r["id"], [])
        for flag in ("requires_confirmation", "is_write", "approved", "enabled"):
            r[flag] = bool(r[flag])
    return rows


def get_action(user_id: int, action_id: int) -> dict | None:
    row = q_one("SELECT * FROM custom_actions WHERE id = ? AND user_id = ?", (action_id, user_id))
    return _hydrate([row])[0] if row else None


def list_actions(user_id: int) -> list[dict]:
    return _hydrate(q_all("SELECT * FROM custom_actions WHERE user_id = ? ORDER BY name", (user_id,)))


def public_view(a: dict) -> dict:
    """Action as shown to the browser. Secrets are never included."""
    conn = q_one("SELECT name FROM database_connections WHERE id = ?", (a["connection_id"],)) if a["connection_id"] else None
    return {
        "id": a["id"], "name": a["name"], "tool_name": a["tool_name"], "description": a["description"],
        "when_to_use": a["when_to_use"], "template_type": a["template_type"],
        "template_label": TEMPLATES.get(a["template_type"], {}).get("label", a["template_type"]),
        "connection_id": a["connection_id"], "connection_name": conn["name"] if conn else None,
        "config": a["config"], "parameters": [
            {k: p[k] for k in ("name", "label", "param_type", "description", "required", "maps_to", "extra")} for p in a["parameters"]],
        "requires_confirmation": a["requires_confirmation"] or a["is_write"], "is_write": a["is_write"],
        "approved": a["approved"], "needs_approval": a["is_write"], "enabled": a["enabled"],
        "has_secret": bool(a["secrets_encrypted"]),
        "created_at": a["created_at"], "updated_at": a["updated_at"],
    }


def _unique_tool_name(user_id: int, base: str, exclude_id: int | None = None) -> str:
    name, i = base, 2
    while True:
        row = q_one("SELECT id FROM custom_actions WHERE user_id = ? AND tool_name = ?", (user_id, name))
        if not row or row["id"] == exclude_id:
            return name
        name = f"{base[:44]}_{i}"
        i += 1


def _prepare(user_id: int, data: dict, existing: dict | None) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Invalid request body.")
    errors: dict[str, str] = {}
    name = clean_text(data.get("name"), "name", errors, min_len=2, max_len=80, required=True)
    description = clean_text(data.get("description"), "description", errors, max_len=500, required=True)
    when = clean_text(data.get("when_to_use"), "when_to_use", errors, max_len=500)
    ttype = data.get("template_type") if not existing else existing["template_type"]
    if ttype not in TEMPLATES:
        errors["template_type"] = "Choose an action type."
    tool_name = clean_text(data.get("tool_name"), "tool_name", errors, max_len=48).lower()
    if tool_name and not TOOL_NAME_RE.match(tool_name):
        errors["tool_name"] = "Use 3-48 lowercase letters, numbers or underscores, starting with a letter."
    if errors:
        raise ValidationError(errors)
    tool_name = tool_name or slugify(name, 44)
    if len(tool_name) < 3:
        tool_name = (tool_name + "_action")[:44]

    connection_id = data.get("connection_id")
    schema = None
    if TEMPLATES[ttype]["needs_connection"]:
        row = conn_svc.get_connection_row(user_id, connection_id) if isinstance(connection_id, int) else None
        if not row:
            raise ValidationError({"connection_id": "Choose a database connection."})
        try:
            schema = conn_svc.schema_for(row)
        except Exception as exc:  # DatabaseError etc.
            raise ValidationError({"connection_id": f"Could not read the database: {exc}"}) from exc
    else:
        connection_id = None

    config, params = validate_action_config(ttype, data.get("config") or {}, data.get("parameters"), schema)

    # secrets (REST auth header value): empty on update = keep existing
    secrets_enc = existing["secrets_encrypted"] if existing else ""
    if ttype == "rest_api":
        secret_in = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
        value = secret_in.get("auth_value")
        if isinstance(value, str) and value.strip():
            if len(value) > 500:
                raise ValidationError({"auth_value": "Too long."})
            secrets_enc = encrypt_text(dumps({"auth_value": value.strip()}))
        if secret_in.get("clear") or not config.get("auth_header"):
            secrets_enc = ""
        elif config.get("auth_header") and not secrets_enc:
            raise ValidationError({"auth_value": "Enter the value for the authentication header."})
    else:
        secrets_enc = ""

    write = is_write_action(ttype, config)
    return {
        "name": name, "tool_name": _unique_tool_name(user_id, tool_name, existing["id"] if existing else None),
        "description": description, "when_to_use": when, "template_type": ttype, "connection_id": connection_id,
        "config": config, "parameters": params, "secrets_encrypted": secrets_enc,
        "requires_confirmation": 1 if (write or data.get("requires_confirmation")) else 0,
        "is_write": 1 if write else 0, "enabled": 1 if data.get("enabled", True) else 0,
    }


def _save_params(action_id: int, params: list[dict]) -> None:
    db = get_db()
    db.execute("DELETE FROM action_parameters WHERE action_id = ?", (action_id,))
    for pos, p in enumerate(params):
        db.execute(
            "INSERT INTO action_parameters (action_id, name, label, param_type, description, required, maps_to, extra, position) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (action_id, p["name"], p["label"], p["param_type"], p["description"], 1 if p["required"] else 0,
             p["maps_to"], dumps(p["extra"]), pos),
        )
    db.commit()


def create_action(user_id: int, data: dict) -> dict:
    p = _prepare(user_id, data, None)
    new_id = execute(
        "INSERT INTO custom_actions (user_id, name, tool_name, description, when_to_use, template_type, connection_id, config, "
        "secrets_encrypted, requires_confirmation, is_write, approved, enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (user_id, p["name"], p["tool_name"], p["description"], p["when_to_use"], p["template_type"], p["connection_id"],
         dumps(p["config"]), p["secrets_encrypted"], p["requires_confirmation"], p["is_write"], p["enabled"]),
    )
    _save_params(new_id, p["parameters"])
    return get_action(user_id, new_id)


def update_action(user_id: int, action_id: int, data: dict) -> dict | None:
    existing = get_action(user_id, action_id)
    if not existing:
        return None
    p = _prepare(user_id, data, existing)
    # Any edit to a sensitive action revokes its approval: it must be re-reviewed.
    execute_rowcount(
        "UPDATE custom_actions SET name=?, tool_name=?, description=?, when_to_use=?, connection_id=?, config=?, secrets_encrypted=?, "
        "requires_confirmation=?, is_write=?, approved=0, enabled=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
        (p["name"], p["tool_name"], p["description"], p["when_to_use"], p["connection_id"], dumps(p["config"]),
         p["secrets_encrypted"], p["requires_confirmation"], p["is_write"], p["enabled"], action_id),
    )
    _save_params(action_id, p["parameters"])
    return get_action(user_id, action_id)


def delete_action(user_id: int, action_id: int) -> bool:
    ok = execute_rowcount("DELETE FROM custom_actions WHERE id = ? AND user_id = ?", (action_id, user_id)) > 0
    if ok:  # remove dangling ids from chatbot settings
        for row in q_all("SELECT s.chatbot_id, s.enabled_actions FROM chatbot_settings s JOIN chatbots c ON c.id = s.chatbot_id WHERE c.user_id = ?", (user_id,)):
            ids = [i for i in loads(row["enabled_actions"], []) if i != action_id]
            execute_rowcount("UPDATE chatbot_settings SET enabled_actions = ? WHERE chatbot_id = ?", (dumps(ids), row["chatbot_id"]))
    return ok


def set_approval(user_id: int, action_id: int, approved: bool) -> dict | None:
    a = get_action(user_id, action_id)
    if not a:
        return None
    if not a["is_write"]:
        raise ValidationError("This action only reads data and does not need approval.")
    execute_rowcount("UPDATE custom_actions SET approved = ? WHERE id = ?", (1 if approved else 0, action_id))
    return get_action(user_id, action_id)


def set_enabled(user_id: int, action_id: int, enabled: bool) -> dict | None:
    if not get_action(user_id, action_id):
        return None
    execute_rowcount("UPDATE custom_actions SET enabled = ? WHERE id = ?", (1 if enabled else 0, action_id))
    return get_action(user_id, action_id)


def secret_values(action: dict) -> dict:
    if not action["secrets_encrypted"]:
        return {}
    return loads(decrypt_text(action["secrets_encrypted"]), {})


# --------------------------------------------------------------------------- LLM exposure


def is_callable(action: dict) -> bool:
    """Server-side gate: only enabled actions (and approved ones, if sensitive) may ever run."""
    return action["enabled"] and (not action["is_write"] or action["approved"])


def allowed_actions_for_bot(user_id: int, chatbot_id: int) -> dict[str, dict]:
    """tool_name -> action, for actions that the chatbot enables AND the owner has approved."""
    settings = q_one("SELECT enabled_actions FROM chatbot_settings WHERE chatbot_id = ?", (chatbot_id,))
    ids = loads(settings["enabled_actions"], []) if settings else []
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = _hydrate(q_all(f"SELECT * FROM custom_actions WHERE user_id = ? AND id IN ({marks})", [user_id, *ids]))
    return {a["tool_name"]: a for a in rows if is_callable(a)}


_JSON_TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}


def tool_schema(a: dict) -> dict:
    props: dict[str, Any] = {}
    required = []
    for p in a["parameters"]:
        spec: dict[str, Any] = {"type": _JSON_TYPES[p["param_type"]], "description": p["description"] or p["label"]}
        if p["extra"].get("choices"):
            spec["enum"] = p["extra"]["choices"]
        props[p["name"]] = spec
        if p["required"]:
            required.append(p["name"])
    desc = a["description"]
    if a["when_to_use"]:
        desc += f" Use when: {a['when_to_use']}"
    return {"name": a["tool_name"], "description": desc[:1000],
            "parameters": {"type": "object", "properties": props, "required": required}}
