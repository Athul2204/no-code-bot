"""Intent, commands and rules for custom actions, plus the node-graph model behind the Flow view.

Each action is one standalone pipeline (the "individual" stage):

    [Intent]  --rule-->  [Rule]  --run-->  [Action]

* intent   - Rasa-style intent name, e.g. ``ask_search_products``
* commands - phrases or slash-commands that trigger the intent, e.g. ``/products``, ``show me tents``
* rules    - conditions that must hold before the action runs (Rasa ``rules.yml`` compatible)

Nothing here executes code. Everything is plain data that is validated, shown to the LLM as guidance,
enforced by the executor (required inputs) and exported to Rasa.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

INTENT_RE = re.compile(r"^[a-z][a-z0-9_]{2,59}$")
MAX_COMMANDS, MAX_COMMAND_LEN = 15, 120
MAX_RULES, MAX_RULE_NAME, MAX_REPLY = 8, 80, 300
ON_MISSING = ("ask", "skip")

_SLUG = re.compile(r"[^a-z0-9]+")


def default_intent(tool_name: str) -> str:
    return f"ask_{tool_name}"[:60]


def _text(value: Any, max_len: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_len]


def clean_intent(value: Any, tool_name: str, errors: dict) -> str:
    raw = _text(value, 60).lower().replace(" ", "_").replace("-", "_")
    if not raw:
        return default_intent(tool_name)
    if not INTENT_RE.match(raw):
        errors["intent"] = "Use 3-60 lowercase letters, numbers or underscores, starting with a letter."
    return raw


def clean_commands(value: Any, errors: dict) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):  # one per line is accepted from the form
        value = value.splitlines()
    if not isinstance(value, list) or len(value) > MAX_COMMANDS * 3:
        errors["commands"] = f"Add at most {MAX_COMMANDS} commands."
        return []
    out: list[str] = []
    for item in value:
        cmd = _text(item, MAX_COMMAND_LEN + 1)
        if not cmd:
            continue
        if len(cmd) > MAX_COMMAND_LEN:
            errors["commands"] = f"Each command must be at most {MAX_COMMAND_LEN} characters."
        if cmd.startswith("/") and not re.fullmatch(r"/[a-z][a-z0-9_]{1,40}", cmd):
            errors["commands"] = "Slash-commands look like /products (lowercase letters, numbers, underscores)."
        if cmd.lower() not in {c.lower() for c in out}:
            out.append(cmd)
    if len(out) > MAX_COMMANDS:
        errors["commands"] = f"Add at most {MAX_COMMANDS} commands."
    return out[:MAX_COMMANDS]


STEP_KEYS = {"intent", "action", "slot_was_set", "active_loop", "wait_for_user_input", "entities"}
COND_KEYS = {"slot_was_set", "active_loop"}
RULE_KEYS = {"rule", "condition", "steps", "conversation_start", "conversation_stop", "wait_for_user_input"}
ACTION_RE = re.compile(r"^(utter|action)_[a-z0-9_]{1,80}$")


def target_for(template_type: str, has_params: bool, tool_name: str) -> str:
    """The Rasa action/response an intent should reach (mirrors the exporter)."""
    if template_type == "static_response" and not has_params:
        return f"utter_{tool_name}"
    return f"action_{tool_name}"


def missing_response_name(tool_name: str, i: int) -> str:
    return f"utter_{tool_name}_missing_{i + 1}"[:90]


def rule_to_rasa(rule: dict, intent: str, target: str, tool_name: str, i: int = 0) -> list[dict]:
    """Structured rule -> Rasa rules.yml entries (a list of rule dicts)."""
    requires = rule.get("requires") or []
    run: dict = {"rule": rule["name"]}
    if requires:
        run["condition"] = [{"slot_was_set": [{n: True} for n in requires]}]
    run["steps"] = [{"intent": intent}, {"action": target}]
    out = [run]
    if requires and rule.get("on_missing", "ask") == "ask" and rule.get("reply_if_missing"):
        out.append({"rule": f"{rule['name']} (ask for missing)",
                    "condition": [{"slot_was_set": [{requires[0]: None}]}],
                    "steps": [{"intent": intent}, {"action": missing_response_name(tool_name, i)}]})
    return out


def dump_rules_yaml(items: list[dict]) -> str:
    return yaml.safe_dump(items, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def parse_rule_yaml(text: str, intent: str, target: str, key: str, errors: dict) -> list[dict]:
    """Validate hand-edited Rasa rule YAML. Only this action's intent and its own utter_/action_ steps are allowed."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errors[key] = "Invalid YAML: " + str(getattr(exc, "problem", exc))[:120]
        return []
    if isinstance(doc, dict) and "rules" in doc:
        doc = doc["rules"]
    items = [doc] if isinstance(doc, dict) else doc
    if not (isinstance(items, list) and 1 <= len(items) <= 4 and all(isinstance(x, dict) for x in items)):
        errors[key] = "Write one Rasa rule (rule: / condition: / steps:)."
        return []
    for r in items:
        if set(r) - RULE_KEYS or not isinstance(r.get("rule"), str) or not r["rule"].strip():
            errors[key] = "Each rule needs a 'rule:' name and only Rasa rule keys (rule, condition, steps)."
            return []
        steps = r.get("steps")
        if not (isinstance(steps, list) and steps and all(isinstance(x, dict) and set(x) <= STEP_KEYS for x in steps)):
            errors[key] = "'steps' must be a list of Rasa steps (intent, action, slot_was_set, active_loop)."
            return []
        for c in r.get("condition") or []:
            if not (isinstance(c, dict) and set(c) <= COND_KEYS):
                errors[key] = "'condition' may only contain slot_was_set or active_loop."
                return []
        for st in steps:
            if "intent" in st and st["intent"] != intent:
                errors[key] = f"This rule may only use this action's intent '{intent}'."
                return []
            act = st.get("action")
            if act is not None and not (act == "action_listen" or (isinstance(act, str) and ACTION_RE.match(act) and (act == target or act.startswith("utter_")))):
                errors[key] = f"Action '{act}' is not allowed. Use '{target}', an utter_ response or action_listen."
                return []
    return items


def clean_rules(value: Any, param_names: set[str], errors: dict, *, intent: str, target: str, tool_name: str) -> list[dict]:
    """Rule = {name, requires, on_missing, reply_if_missing, yaml}. `yaml` is the real Rasa rule and is what gets exported."""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_RULES:
        errors["rules"] = f"Add at most {MAX_RULES} rules."
        return []
    out: list[dict] = []
    for i, r in enumerate(value):
        if not isinstance(r, dict):
            errors["rules"] = "Invalid rule."
            continue
        name = _text(r.get("name"), MAX_RULE_NAME)
        requires = r.get("requires") or []
        if not (isinstance(requires, list) and all(isinstance(x, str) for x in requires)):
            errors[f"rules.{i}.requires"] = "Choose inputs from the list."
            requires = []
        unknown = [x for x in requires if x not in param_names]
        if unknown:
            errors[f"rules.{i}.requires"] = "Unknown input(s): " + ", ".join(unknown)
        on_missing = r.get("on_missing") or "ask"
        if on_missing not in ON_MISSING:
            errors[f"rules.{i}.on_missing"] = "Choose ask or skip."
            on_missing = "ask"
        raw_yaml = (r.get("yaml") or "").strip()
        if raw_yaml:  # hand-edited Rasa YAML wins; the name comes from it
            items = parse_rule_yaml(raw_yaml, intent, target, f"rules.{i}.yaml", errors)
            name = name or (items[0]["rule"][:MAX_RULE_NAME] if items else "")
        if not name:
            errors[f"rules.{i}.name"] = "Give the rule a name."
        rule = {"name": name, "requires": [x for x in dict.fromkeys(requires) if x in param_names], "on_missing": on_missing,
                "reply_if_missing": _text(r.get("reply_if_missing"), MAX_REPLY)}
        if not raw_yaml and name:
            raw_yaml = dump_rules_yaml(rule_to_rasa(rule, intent, target, tool_name, i)).strip()
        rule["yaml"] = raw_yaml
        out.append(rule)
    return out


# --------------------------------------------------------------------------- runtime helpers


def rule_missing(rules: list[dict], args: dict) -> list[str]:
    """Inputs demanded by 'ask' rules that the user has not supplied yet (enforced by the executor)."""
    missing: list[str] = []
    for r in rules or []:
        if r.get("on_missing", "ask") != "ask":
            continue
        for n in r.get("requires", []):
            if args.get(n) in (None, "") and n not in missing:
                missing.append(n)
    return missing


def guidance_text(intent: str, commands: list[str], rules: list[dict]) -> str:
    """Compact text appended to the tool description so the LLM follows intent, commands and rules."""
    parts = []
    if intent:
        parts.append(f"Intent: {intent}.")
    if commands:
        parts.append("Triggered by: " + "; ".join(commands[:8]) + ".")
    for r in rules or []:
        if r.get("requires"):
            verb = "Only run once you have" if r.get("on_missing", "ask") == "ask" else "Skip unless you have"
            parts.append(f"Rule '{r['name']}': {verb} {', '.join(r['requires'])}.")
    return " ".join(parts)


def match_command(text: str, commands: list[str]) -> bool:
    """True when the user's message is (or starts with) one of the slash-commands, or equals a phrase."""
    t = (text or "").strip().lower()
    for c in commands or []:
        c = c.lower()
        if c.startswith("/") and (t == c or t.startswith(c + " ")):
            return True
        if not c.startswith("/") and t == c:
            return True
    return False


# --------------------------------------------------------------------------- graph model

COL_X = (40, 330, 620)  # intent | rule | action
ROW_H = 150
KINDS = {"intent": "Intent", "rule": "Rule", "action": "Action"}


def build_graph(actions: list[dict]) -> dict:
    """Nodes + edges for the Flow view. One independent lane per action (no cross-action links yet).

    Edge styles follow the reference picture: solid = always taken, dashed = optional / conditional.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    for lane, a in enumerate(actions):
        y = 30 + lane * ROW_H
        base = f"a{a['id']}"
        intent = a.get("intent") or default_intent(a["tool_name"])
        cmds = a.get("commands") or []
        rules = a.get("rules") or []
        n_params = len(a.get("parameters") or [])
        n_req = len([p for p in a.get("parameters") or [] if p.get("required")])
        nodes.append({"id": f"{base}_intent", "kind": "intent", "action_id": a["id"], "label": intent, "icon": "intent",
                      "meta": f"{len(cmds)} command{'s' if len(cmds) != 1 else ''}", "fill": min(100, 20 + 15 * len(cmds)),
                      "commands": cmds, "x": COL_X[0], "y": y})
        nodes.append({"id": f"{base}_action", "kind": "action", "action_id": a["id"], "label": a["name"],
                      "icon": a.get("template_type", "custom_form"), "meta": f"{n_req}/{n_params} required inputs",
                      "fill": 100 if not n_params else int(100 * n_req / n_params) or 15,
                      "enabled": bool(a.get("enabled", True)), "write": bool(a.get("is_write")), "x": COL_X[2], "y": y})
        if not rules:  # no rule: direct, always-run link
            edges.append({"id": f"{base}_e0", "from": f"{base}_intent", "to": f"{base}_action", "style": "solid", "label": ""})
            continue
        for i, r in enumerate(rules):
            rid = f"{base}_rule{i}"
            nodes.append({"id": rid, "kind": "rule", "action_id": a["id"], "label": r["name"], "icon": "rule",
                          "meta": ("needs " + ", ".join(r["requires"])) if r["requires"] else "always",
                          "fill": 40 + 10 * len(r["requires"]) if r["requires"] else 20, "x": COL_X[1], "y": y + i * 70 - (len(rules) - 1) * 35})
            dashed = "dashed" if r["on_missing"] == "skip" else "solid"
            edges.append({"id": f"{rid}_in", "from": f"{base}_intent", "to": rid, "style": "solid", "label": ""})
            edges.append({"id": f"{rid}_out", "from": rid, "to": f"{base}_action", "style": dashed, "label": r["on_missing"]})
    return {"nodes": nodes, "edges": edges, "height": 60 + max(1, len(actions)) * ROW_H, "stage": "individual"}


def suggest_defaults(a: dict) -> dict:
    """Sensible starting intent / commands / rules so every action 'contains' them from the first save."""
    tool = a["tool_name"]
    words = a["name"].lower()
    cmds = [f"/{tool[:40]}", words, f"I want to {words}"]
    required = [p["name"] for p in a.get("parameters") or [] if p.get("required")]
    rules = [{"name": f"Collect inputs for {a['name']}", "requires": required, "on_missing": "ask", "reply_if_missing": ""}] if required else []
    return {"intent": default_intent(tool), "commands": cmds, "rules": rules}
