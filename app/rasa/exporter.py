"""Generate a Rasa project from a chatbot (+ optional imported files). Preview first; nothing is written to disk."""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import yaml

from ..services import actions as action_svc
from ..validation import slugify
from .importer import RASA_BUILTIN_ACTIONS, get_import


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_presenter)


def _dump(doc: Any) -> str:
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def _load_imports(user_id: int, import_ids: list[int]) -> list[tuple[str, Any]]:
    docs = []
    for i in import_ids:
        imp = get_import(user_id, i)
        if imp and imp["parse_ok"]:
            docs.append((imp["filename"], yaml.safe_load(imp["content"])))
    return docs


def _intent_name(item) -> str:
    return next(iter(item)) if isinstance(item, dict) else str(item)


def build_export(user_id: int, bot: dict, import_ids: list[int]) -> dict:
    docs = [d for _, d in _load_imports(user_id, import_ids) if isinstance(d, dict)]
    actions = [a for a in action_svc.list_actions(user_id) if a["id"] in bot["enabled_actions"] and a["enabled"]]

    intents: list[Any] = []
    responses: dict[str, Any] = {}
    domain_actions: list[str] = []
    nlu_items: list[dict] = []
    rules: list[Any] = []
    stories: list[Any] = []
    config: dict | None = None
    notes: list[str] = []

    for d in docs:
        for i in d.get("intents") or []:
            if _intent_name(i) not in {_intent_name(x) for x in intents}:
                intents.append(i)
        for k, v in (d.get("responses") or {}).items():
            responses.setdefault(k, v)
        for a in d.get("actions") or []:
            if a not in domain_actions:
                domain_actions.append(a)
        nlu_items += [x for x in (d.get("nlu") or []) if isinstance(x, dict)]
        rules += d.get("rules") or []
        stories += d.get("stories") or []
        if "pipeline" in d or "policies" in d or "recipe" in d:
            config = config or d

    existing_intents = {_intent_name(i) for i in intents}
    if bot["welcome_message"]:
        responses.setdefault("utter_welcome", [{"text": bot["welcome_message"]}])

    py_stubs: list[tuple[str, dict]] = []
    for a in actions:
        intent = f"ask_{a['tool_name']}"
        if intent not in existing_intents:
            intents.append(intent)
            existing_intents.add(intent)
        examples = [a["name"].lower(), f"I want to {a['name'].lower()}"]
        if a["when_to_use"]:
            examples.append(a["when_to_use"][:120])
        nlu_items.append({"intent": intent, "examples": "".join(f"- {e}\n" for e in dict.fromkeys(examples))})
        static_simple = a["template_type"] == "static_response" and not a["parameters"]
        if static_simple:
            uname = f"utter_{a['tool_name']}"
            responses.setdefault(uname, [{"text": a["config"]["response_text"]}])
            rules.append({"rule": f"Answer '{a['name']}'", "steps": [{"intent": intent}, {"action": uname}]})
        else:
            aname = f"action_{a['tool_name']}"
            if aname not in domain_actions:
                domain_actions.append(aname)
            rules.append({"rule": f"Run '{a['name']}'", "steps": [{"intent": intent}, {"action": aname}]})
            py_stubs.append((aname, a))
    if "greet" in existing_intents and "utter_welcome" in responses:
        rules.append({"rule": "Welcome the user when they greet", "steps": [{"intent": "greet"}, {"action": "utter_welcome"}]})

    slug = slugify(bot["name"], 40)
    domain = {"version": "3.1", "intents": intents}
    if responses:
        domain["responses"] = responses
    if domain_actions:
        domain["actions"] = domain_actions
    domain["session_config"] = {"session_expiration_time": 60, "carry_over_slots_to_new_session": True}

    files: dict[str, str] = {
        "domain.yml": _dump(domain),
        "data/nlu.yml": _dump({"version": "3.1", "nlu": nlu_items}),
        "data/rules.yml": _dump({"version": "3.1", "rules": rules}),
        "data/stories.yml": _dump({"version": "3.1", "stories": stories}),
    }
    if config:
        keep = {k: v for k, v in config.items() if k in ("recipe", "assistant_id", "language", "pipeline", "policies") and v is not None}
        files["config.yml"] = _dump(keep)
    else:
        files["config.yml"] = f"recipe: default.v1\nassistant_id: {slug}\nlanguage: en\n"
    if py_stubs:
        files["endpoints.yml"] = "action_endpoint:\n  url: \"http://localhost:5055/webhook\"\n"
        files["actions/__init__.py"] = ""
        files["actions/actions.py"] = _actions_stub(py_stubs)
        notes.append("Custom actions are exported as stubs. Their real behaviour (database, API calls) runs inside this builder, not in Rasa.")
    notes.append("Example phrases are generated from action names. Add more examples in Rasa for better understanding.")

    return {"files": files, "validation": _validate(files, domain), "notes": notes, "bot_slug": slug}


def _actions_stub(stubs: list[tuple[str, dict]]) -> str:
    out = ["from typing import Any, Dict, List, Text", "", "from rasa_sdk import Action, Tracker", "from rasa_sdk.executor import CollectingDispatcher", ""]
    for aname, a in stubs:
        cls = "".join(w.capitalize() for w in aname.split("_"))
        desc = a["name"].replace('"', "'")
        out += ["", f"class {cls}(Action):", f"    def name(self) -> Text:", f'        return "{aname}"', "",
                "    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:",
                f'        dispatcher.utter_message(text="{desc}: connect this stub to your logic.")', "        return []", ""]
    return "\n".join(out)


def _validate(files: dict[str, str], domain: dict) -> dict:
    errors, warnings = [], []
    parsed: dict[str, Any] = {}
    for path, content in files.items():
        if path.endswith((".yml", ".yaml")):
            try:
                parsed[path] = yaml.safe_load(content)
            except yaml.YAMLError as exc:
                errors.append(f"{path}: invalid YAML ({exc})")
    known_intents = {_intent_name(i) for i in domain.get("intents", [])}
    known_actions = set(domain.get("actions", [])) | set(domain.get("responses", {})) | RASA_BUILTIN_ACTIONS
    for path in ("data/rules.yml", "data/stories.yml"):
        doc = parsed.get(path) or {}
        for block in (doc.get("rules") or []) + (doc.get("stories") or []):
            for step in (block or {}).get("steps", []) if isinstance(block, dict) else []:
                if not isinstance(step, dict):
                    continue
                if "intent" in step and step["intent"] not in known_intents:
                    errors.append(f"{path}: intent '{step['intent']}' is not listed in domain.yml")
                if "action" in step and step["action"] not in known_actions and not str(step["action"]).endswith("_form"):
                    errors.append(f"{path}: action '{step['action']}' is not listed in domain.yml")
    if not known_intents:
        warnings.append("No intents were found. Import Rasa files or enable actions on the chatbot.")
    if not (parsed.get("data/nlu.yml") or {}).get("nlu"):
        warnings.append("There are no training examples (nlu).")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            z.writestr(path, content)
    return buf.getvalue()
