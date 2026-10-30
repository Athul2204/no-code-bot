"""JSON API. Every route requires login; state-changing requests also require a CSRF token."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request

from ..actions.executor import execute_action
from ..actions.templates import template_catalog
from ..db import q_all, q_one, execute_rowcount, loads
from ..integrations.databases import DatabaseError, build_adapter, engine_choices
from ..llm import (PROVIDERS, SETUP_MESSAGE, LLMError, effective_settings, get_provider, get_user_setting, set_user_setting)
from ..rasa import exporter as rasa_exporter
from ..rasa import importer as rasa_importer
from ..security import UnsafeURLError, current_user, login_required, rate_limited, validate_url_syntax
from ..services import actions as action_svc
from ..services import chat as chat_svc
from ..services import chatbots as bot_svc
from ..services import connections as conn_svc
from ..validation import ValidationError, clean_text
from .helpers import api_error, json_body, not_found

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.before_request
@login_required
def _require_login():
    return None


def uid() -> int:
    return current_user()["id"]


# --------------------------------------------------------------------------- status / dashboard


@bp.get("/status")
def status():
    return jsonify({
        "llm_configured": get_provider(uid()) is not None,
        "rasa_enabled": current_app.config["RASA_ENABLED"],
        "python_actions_enabled": current_app.config["ENABLE_PYTHON_ACTIONS"],
        "email": current_user()["email"],
    })


@bp.get("/dashboard")
def dashboard():
    u = uid()
    count = lambda sql: q_one(sql, (u,))["n"]  # noqa: E731
    return jsonify({
        "counts": {
            "chatbots": count("SELECT count(*) n FROM chatbots WHERE user_id = ?"),
            "actions": count("SELECT count(*) n FROM custom_actions WHERE user_id = ?"),
            "connections": count("SELECT count(*) n FROM database_connections WHERE user_id = ?"),
            "conversations": count("SELECT count(*) n FROM conversations WHERE user_id = ?"),
        },
        "llm_configured": get_provider(u) is not None,
        "pending_approvals": count("SELECT count(*) n FROM custom_actions WHERE user_id = ? AND is_write = 1 AND approved = 0"),
        "recent_logs": q_all("SELECT id, action_name, status, result_summary, test_mode, source, created_at FROM action_execution_logs WHERE user_id = ? ORDER BY id DESC LIMIT 6", (u,)),
    })


# --------------------------------------------------------------------------- chatbots


@bp.get("/chatbots")
def chatbots_list():
    return jsonify({"chatbots": bot_svc.list_bots(uid())})


@bp.post("/chatbots")
def chatbots_create():
    return jsonify(bot_svc.create_bot(uid(), json_body())), 201


@bp.get("/chatbots/<int:bot_id>")
def chatbots_get(bot_id):
    bot = bot_svc.get_bot(uid(), bot_id)
    return jsonify(bot) if bot else not_found("Chatbot")


@bp.put("/chatbots/<int:bot_id>")
def chatbots_update(bot_id):
    bot = bot_svc.update_bot(uid(), bot_id, json_body())
    return jsonify(bot) if bot else not_found("Chatbot")


@bp.delete("/chatbots/<int:bot_id>")
def chatbots_delete(bot_id):
    return jsonify({"ok": True}) if bot_svc.delete_bot(uid(), bot_id) else not_found("Chatbot")


@bp.post("/chatbots/<int:bot_id>/duplicate")
def chatbots_duplicate(bot_id):
    bot = bot_svc.duplicate_bot(uid(), bot_id)
    return (jsonify(bot), 201) if bot else not_found("Chatbot")


@bp.get("/chatbots/<int:bot_id>/versions")
def chatbots_versions(bot_id):
    versions = bot_svc.list_versions(uid(), bot_id)
    return jsonify({"versions": versions}) if versions is not None else not_found("Chatbot")


@bp.post("/chatbots/<int:bot_id>/versions/<int:version_id>/restore")
def chatbots_restore(bot_id, version_id):
    bot = bot_svc.restore_version(uid(), bot_id, version_id)
    return jsonify(bot) if bot else not_found("Version")


# --------------------------------------------------------------------------- connections


@bp.get("/connections")
def connections_list():
    return jsonify({"connections": conn_svc.list_connections(uid()), "engines": engine_choices(), "files": conn_svc.available_files()})


@bp.post("/connections")
def connections_create():
    return jsonify(conn_svc.create_connection(uid(), json_body())), 201


@bp.put("/connections/<int:conn_id>")
def connections_update(conn_id):
    c = conn_svc.update_connection(uid(), conn_id, json_body())
    return jsonify(c) if c else not_found("Connection")


@bp.delete("/connections/<int:conn_id>")
def connections_delete(conn_id):
    return jsonify({"ok": True}) if conn_svc.delete_connection(uid(), conn_id) else not_found("Connection")


@bp.post("/connections/upload")
def connections_upload():
    f = request.files.get("file")
    if not f:
        raise ValidationError({"file": "Choose a file to upload."})
    return jsonify({"file": conn_svc.save_uploaded_database(f), "files": conn_svc.available_files()}), 201


@bp.post("/connections/test")
def connections_test_unsaved():
    """Test a connection before saving it (credentials never come back)."""
    name, db_type, config = conn_svc.validate_payload({**json_body(), "name": "test connection"})
    try:
        result = build_adapter(db_type, config, current_app.config["USER_DB_DIR"], False).test()
    except DatabaseError as exc:
        result = {"ok": False, "message": str(exc)}
    return jsonify(result)


def _conn_or_404(conn_id):
    return conn_svc.get_connection_row(uid(), conn_id)


@bp.post("/connections/<int:conn_id>/test")
def connections_test(conn_id):
    row = _conn_or_404(conn_id)
    if not row:
        return not_found("Connection")
    try:
        return jsonify(conn_svc.adapter_for(row).test())
    except DatabaseError as exc:
        return jsonify({"ok": False, "message": str(exc)})


@bp.get("/connections/<int:conn_id>/schema")
def connections_schema(conn_id):
    row = _conn_or_404(conn_id)
    if not row:
        return not_found("Connection")
    try:
        return jsonify({"tables": conn_svc.schema_for(row)})
    except DatabaseError as exc:
        return api_error(str(exc), 400)


@bp.post("/connections/<int:conn_id>/preview")
def connections_preview(conn_id):
    row = _conn_or_404(conn_id)
    if not row:
        return not_found("Connection")
    data = json_body()
    table, columns = data.get("table"), data.get("columns") or None
    if not isinstance(table, str) or (columns is not None and not (isinstance(columns, list) and all(isinstance(c, str) for c in columns))):
        raise ValidationError("Choose a table and columns.")
    try:
        rows = conn_svc.adapter_for(row).select(table, columns, [], min(int(data.get("limit", 20)), 50))
    except DatabaseError as exc:
        return api_error(str(exc), 400)
    return jsonify({"rows": rows})


@bp.post("/connections/<int:conn_id>/writes")
def connections_writes(conn_id):
    data = json_body()
    c = conn_svc.set_allow_writes(uid(), conn_id, bool(data.get("allow")))
    return jsonify(c) if c else not_found("Connection")


# --------------------------------------------------------------------------- actions


@bp.get("/action-templates")
def action_templates():
    return jsonify({"templates": template_catalog(), "python_enabled": current_app.config["ENABLE_PYTHON_ACTIONS"]})


@bp.get("/actions")
def actions_list():
    return jsonify({"actions": [action_svc.public_view(a) for a in action_svc.list_actions(uid())]})


@bp.post("/actions")
def actions_create():
    return jsonify(action_svc.public_view(action_svc.create_action(uid(), json_body()))), 201


@bp.get("/actions/<int:action_id>")
def actions_get(action_id):
    a = action_svc.get_action(uid(), action_id)
    return jsonify(action_svc.public_view(a)) if a else not_found("Action")


@bp.put("/actions/<int:action_id>")
def actions_update(action_id):
    a = action_svc.update_action(uid(), action_id, json_body())
    return jsonify(action_svc.public_view(a)) if a else not_found("Action")


@bp.delete("/actions/<int:action_id>")
def actions_delete(action_id):
    return jsonify({"ok": True}) if action_svc.delete_action(uid(), action_id) else not_found("Action")


@bp.post("/actions/<int:action_id>/approve")
def actions_approve(action_id):
    a = action_svc.set_approval(uid(), action_id, bool(json_body().get("approved", True)))
    return jsonify(action_svc.public_view(a)) if a else not_found("Action")


@bp.post("/actions/<int:action_id>/enabled")
def actions_enabled(action_id):
    a = action_svc.set_enabled(uid(), action_id, bool(json_body().get("enabled", True)))
    return jsonify(action_svc.public_view(a)) if a else not_found("Action")


@bp.post("/actions/<int:action_id>/test")
def actions_test(action_id):
    """Run an action directly (no LLM needed). Test mode (default) never changes real data."""
    a = action_svc.get_action(uid(), action_id)
    if not a:
        return not_found("Action")
    data = json_body()
    result = execute_action(a, data.get("arguments") or {}, user_id=uid(), test_mode=bool(data.get("test_mode", True)), source="manual_test")
    return jsonify(result.to_ui())


@bp.get("/logs")
def logs_list():
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50
    rows = q_all("SELECT id, action_name, arguments, status, result_summary, error, test_mode, source, duration_ms, created_at "
                 "FROM action_execution_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?", (uid(), limit))
    for r in rows:
        r["arguments"] = loads(r["arguments"], {})
    return jsonify({"logs": rows})


# --------------------------------------------------------------------------- chat


def _events_response(conv: dict, result: dict):
    return jsonify({"conversation_id": conv["id"], "title": conv["title"], "test_mode": bool(conv["test_mode"]), **result})


@bp.post("/chatbots/<int:bot_id>/chat")
@rate_limited("chat")
def chat(bot_id):
    user = current_user()
    bot = bot_svc.get_bot(user["id"], bot_id)
    if not bot:
        return not_found("Chatbot")
    data = json_body()
    errors: dict = {}
    message = clean_text(data.get("message"), "message", errors, min_len=1, max_len=4000, required=True)
    if errors:
        raise ValidationError(errors)
    conv_id = data.get("conversation_id")
    test_mode = bool(data.get("test_mode", True))
    if conv_id:
        conv = chat_svc.get_conversation(user["id"], conv_id) if isinstance(conv_id, int) else None
        if not conv or conv["chatbot_id"] != bot_id:
            return not_found("Conversation")
        if "test_mode" in data and bool(conv["test_mode"]) != test_mode:
            execute_rowcount("UPDATE conversations SET test_mode = ? WHERE id = ?", (1 if test_mode else 0, conv["id"]))
            conv["test_mode"] = 1 if test_mode else 0
    else:
        conv = chat_svc.create_conversation(user["id"], bot, test_mode)
    result = chat_svc.send_message(user, bot, conv, message)
    conv = chat_svc.get_conversation(user["id"], conv["id"])
    return _events_response(conv, result)


@bp.post("/chat/confirm")
@rate_limited("chat")
def chat_confirm():
    data = json_body()
    token = data.get("token")
    if not isinstance(token, str):
        raise ValidationError("Missing confirmation token.")
    result = chat_svc.resolve_confirmation(current_user(), token, bool(data.get("approve")))
    if result is None:
        return not_found("Confirmation")
    conv = chat_svc.get_conversation(uid(), result["conversation_id"])
    return _events_response(conv, result)


@bp.get("/chatbots/<int:bot_id>/conversations")
def conversations_list(bot_id):
    if not bot_svc.get_bot(uid(), bot_id):
        return not_found("Chatbot")
    rows = q_all("SELECT id, title, test_mode, created_at, updated_at FROM conversations WHERE chatbot_id = ? AND user_id = ? ORDER BY updated_at DESC, id DESC LIMIT 50", (bot_id, uid()))
    return jsonify({"conversations": rows})


@bp.get("/conversations/<int:conv_id>")
def conversations_get(conv_id):
    conv = chat_svc.get_conversation(uid(), conv_id)
    if not conv:
        return not_found("Conversation")
    return jsonify({"conversation": {"id": conv["id"], "title": conv["title"], "test_mode": bool(conv["test_mode"]), "chatbot_id": conv["chatbot_id"]},
                    "events": chat_svc.messages_to_events(conv_id)})


@bp.delete("/conversations/<int:conv_id>")
def conversations_delete(conv_id):
    ok = execute_rowcount("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, uid())) > 0
    return jsonify({"ok": True}) if ok else not_found("Conversation")


@bp.post("/conversations/<int:conv_id>/clear")
def conversations_clear(conv_id):
    if not chat_svc.get_conversation(uid(), conv_id):
        return not_found("Conversation")
    execute_rowcount("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    execute_rowcount("DELETE FROM pending_confirmations WHERE conversation_id = ?", (conv_id,))
    execute_rowcount("UPDATE conversations SET title = 'New conversation' WHERE id = ?", (conv_id,))
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- settings


def _settings_view() -> dict:
    s = effective_settings(uid())
    return {"provider": s["provider"], "model": s["model"], "base_url": s["base_url"], "has_key": bool(s["api_key"]),
            "key_source": s["key_source"], "providers": list(PROVIDERS), "configured": get_provider(uid()) is not None,
            "setup_message": SETUP_MESSAGE,
            "defaults": {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}}


@bp.get("/settings")
def settings_get():
    return jsonify(_settings_view())


@bp.put("/settings")
def settings_put():
    data = json_body()
    errors: dict = {}
    provider = clean_text(data.get("provider"), "provider", errors, max_len=20)
    if provider not in PROVIDERS:
        errors["provider"] = "Choose a provider."
    model = clean_text(data.get("model"), "model", errors, max_len=100)
    base_url = clean_text(data.get("base_url"), "base_url", errors, max_len=300)
    if base_url:
        try:
            validate_url_syntax(base_url)
        except UnsafeURLError as exc:
            errors["base_url"] = str(exc)
    key = data.get("api_key")
    if key is not None and (not isinstance(key, str) or len(key) > 500):
        errors["api_key"] = "Invalid API key."
    if errors:
        raise ValidationError(errors)
    set_user_setting(uid(), "llm_provider", provider)
    set_user_setting(uid(), "llm_model", model)
    set_user_setting(uid(), "llm_base_url", base_url)
    if data.get("clear_api_key"):
        set_user_setting(uid(), "llm_api_key", "")
    elif isinstance(key, str) and key.strip():
        set_user_setting(uid(), "llm_api_key", key.strip(), secret=True)  # encrypted at rest, never returned
    return jsonify(_settings_view())


@bp.post("/settings/test-llm")
@rate_limited("llmtest")
def settings_test_llm():
    provider = get_provider(uid())
    if provider is None:
        return jsonify({"ok": False, "message": SETUP_MESSAGE})
    try:
        return jsonify({"ok": True, "message": f"Connected. The model replied: {provider.test_connection()[:80]}"})
    except LLMError as exc:
        return jsonify({"ok": False, "message": str(exc)})


# --------------------------------------------------------------------------- rasa (optional module)


@bp.before_request
def _rasa_gate():
    if request.path.startswith("/api/rasa") and not current_app.config["RASA_ENABLED"]:
        return api_error("Rasa integration is disabled. Set RASA_ENABLED=true in .env to use it.", 404)
    return None


@bp.get("/rasa/imports")
def rasa_list():
    return jsonify({"imports": rasa_importer.list_imports(uid())})


@bp.post("/rasa/import")
def rasa_import():
    files = [(f.filename, f.read(rasa_importer.MAX_FILE_BYTES + 1)) for f in request.files.getlist("files")]
    return jsonify({"imports": rasa_importer.import_files(uid(), files)}), 201


@bp.post("/rasa/import-sample")
def rasa_import_sample():
    return jsonify({"imports": rasa_importer.import_files(uid(), rasa_importer.sample_files())}), 201


@bp.get("/rasa/imports/<int:import_id>")
def rasa_get(import_id):
    imp = rasa_importer.get_import(uid(), import_id)
    return jsonify(imp) if imp else not_found("Import")


@bp.delete("/rasa/imports/<int:import_id>")
def rasa_delete(import_id):
    return jsonify({"ok": True}) if rasa_importer.delete_import(uid(), import_id) else not_found("Import")


@bp.post("/rasa/imports/<int:import_id>/create-actions")
def rasa_create_actions(import_id):
    return jsonify(rasa_importer.create_actions_from_responses(uid(), import_id))


def _export_args():
    data = json_body()
    bot = bot_svc.get_bot(uid(), data.get("chatbot_id")) if isinstance(data.get("chatbot_id"), int) else None
    if not bot:
        raise ValidationError({"chatbot_id": "Choose a chatbot."})
    ids = data.get("import_ids") or []
    if not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
        raise ValidationError({"import_ids": "Invalid selection."})
    return bot, ids


@bp.post("/rasa/export/preview")
def rasa_export_preview():
    bot, ids = _export_args()
    out = rasa_exporter.build_export(uid(), bot, ids)
    return jsonify({"files": out["files"], "validation": out["validation"], "notes": out["notes"]})


@bp.post("/rasa/export/download")
def rasa_export_download():
    bot, ids = _export_args()
    out = rasa_exporter.build_export(uid(), bot, ids)
    if not out["validation"]["ok"]:
        return api_error("The generated files have validation errors. Fix them before exporting.", 400, validation=out["validation"])
    return Response(rasa_exporter.build_zip(out["files"]), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{out["bot_slug"]}-rasa.zip"'})
