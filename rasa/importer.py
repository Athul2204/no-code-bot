from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from flask import current_app

from ..db import dumps, execute, loads, q_all, q_one, execute_rowcount
from ..validation import ValidationError, slugify

MAX_FILES = 25
MAX_FILE_BYTES = 1024 * 1024
RASA_BUILTIN_ACTIONS = {"action_listen", "action_restart", "action_session_start", "action_default_fallback",
                        "action_deactivate_loop", "action_back", "action_two_stage_fallback", "action_default_ask_affirmation",
                        "action_default_ask_rephrase", "action_unlikely_intent", "action_extract_slots"}


def _summarize(doc) -> dict:
    """Describe what a parsed Rasa YAML file contains."""
    s: dict = {"kinds": [], "intents": [], "responses": {}, "actions": [], "counts": {}}
    if not isinstance(doc, dict):
        return s
    if "nlu" in doc:
        s["kinds"].append("nlu")
        total = 0
        for item in doc.get("nlu") or []:
            if isinstance(item, dict) and "intent" in item:
                s["intents"].append(str(item["intent"]))
                ex = item.get("examples") or ""
                total += len([ln for ln in str(ex).splitlines() if ln.strip().startswith("-")])
        s["counts"]["nlu_examples"] = total
    if "rules" in doc:
        s["kinds"].append("rules")
        s["counts"]["rules"] = len(doc.get("rules") or [])
    if "stories" in doc:
        s["kinds"].append("stories")
        s["counts"]["stories"] = len(doc.get("stories") or [])
    if any(k in doc for k in ("intents", "responses", "actions", "slots", "entities")):
        s["kinds"].append("domain")
        for i in doc.get("intents") or []:
            s["intents"].append(next(iter(i)) if isinstance(i, dict) else str(i))
        for name, variants in (doc.get("responses") or {}).items():
            texts = [v.get("text") for v in (variants or []) if isinstance(v, dict) and v.get("text")]
            s["responses"][str(name)] = texts
        s["actions"] = [str(a) for a in (doc.get("actions") or [])]
    if "pipeline" in doc or "policies" in doc or "recipe" in doc:
        s["kinds"].append("config")
    if "action_endpoint" in doc or "tracker_store" in doc or "models" in doc:
        s["kinds"].append("endpoints")
    if "stories" in doc and "test" in str(doc.get("stories"))[:0]:
        pass
    s["intents"] = sorted(set(s["intents"]))
    return s


def import_files(user_id: int, files: list[tuple[str, bytes]]) -> list[dict]:
    if not files:
        raise ValidationError({"files": "Choose at least one .yml or .yaml file."})
    if len(files) > MAX_FILES:
        raise ValidationError({"files": f"At most {MAX_FILES} files can be imported at once."})
    results = []
    for filename, raw in files:
        name = Path(filename or "").name
        if not re.fullmatch(r"[\w\-. ]{1,120}\.(yml|yaml)", name, re.IGNORECASE):
            raise ValidationError({"files": f"'{name or filename}' is not a .yml or .yaml file."})
        if len(raw) > MAX_FILE_BYTES:
            raise ValidationError({"files": f"'{name}' is larger than 1 MB."})
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError({"files": f"'{name}' is not UTF-8 text."}) from None
        parse_ok, err, summary = 1, "", {}
        try:
            summary = _summarize(yaml.safe_load(text))
        except yaml.YAMLError as exc:
            parse_ok, err = 0, str(getattr(exc, "problem", exc))[:300]
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                err += f" (line {mark.line + 1})"
        new_id = execute(
            "INSERT INTO rasa_imports (user_id, filename, content, content_sha256, parse_ok, parse_error, summary) VALUES (?,?,?,?,?,?,?)",
            (user_id, name, text, hashlib.sha256(raw).hexdigest(), parse_ok, err, dumps(summary)))
        results.append(get_import(user_id, new_id, with_content=False))
    return results


def sample_files() -> list[tuple[str, bytes]]:
    base = Path(current_app.config["SAMPLES_DIR"]) / "rasa_bot"
    paths = sorted(base.rglob("*.yml"))
    counts: dict[str, int] = {}
    for p in paths:
        counts[p.name] = counts.get(p.name, 0) + 1
    # Plain file names, with the folder as a prefix only when two files share a name.
    return [(p.name if counts[p.name] == 1 else f"{p.parent.name}_{p.name}", p.read_bytes()) for p in paths]


def _public(row: dict, with_content: bool) -> dict:
    out = {"id": row["id"], "filename": row["filename"], "parse_ok": bool(row["parse_ok"]), "parse_error": row["parse_error"],
           "summary": loads(row["summary"], {}), "sha256": row["content_sha256"], "created_at": row["created_at"],
           "size": len(row["content"].encode("utf-8"))}
    if with_content:
        out["content"] = row["content"]
    return out


def get_import(user_id: int, import_id: int, with_content: bool = True) -> dict | None:
    row = q_one("SELECT * FROM rasa_imports WHERE id = ? AND user_id = ?", (import_id, user_id))
    return _public(row, with_content) if row else None


def list_imports(user_id: int) -> list[dict]:
    return [_public(r, False) for r in q_all("SELECT * FROM rasa_imports WHERE user_id = ? ORDER BY id DESC", (user_id,))]


def delete_import(user_id: int, import_id: int) -> bool:
    return execute_rowcount("DELETE FROM rasa_imports WHERE id = ? AND user_id = ?", (import_id, user_id)) > 0


def create_actions_from_responses(user_id: int, import_id: int) -> dict:
    """Turn imported `utter_*` responses into 'predefined response' actions (never touches the source file)."""
    from ..services import actions as action_svc

    imp = get_import(user_id, import_id, with_content=False)
    if not imp:
        raise ValidationError("Import not found.")
    created, skipped = [], []
    for name, texts in imp["summary"].get("responses", {}).items():
        if not texts:
            continue
        tool = slugify(name, 44)
        if q_one("SELECT id FROM custom_actions WHERE user_id = ? AND tool_name = ?", (user_id, tool)):
            skipped.append(name)
            continue
        text = re.sub(r"\{([^{}]*)\}", lambda m: f"[{m.group(1)}]", texts[0])[:2000]
        label = "Rasa: " + name.replace("utter_", "").replace("_", " ")
        a = action_svc.create_action(user_id, {
            "name": label, "tool_name": tool, "template_type": "static_response",
            "description": f"Sends the imported Rasa response '{name}'.",
            "when_to_use": f"When the conversation matches the Rasa response '{name}'.",
            "config": {"response_text": text}, "parameters": [],
        })
        created.append({"id": a["id"], "name": a["name"]})
    return {"created": created, "skipped": skipped}
