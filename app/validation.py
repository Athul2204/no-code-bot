"""Input validation shared by routes, services and the action executor."""
from __future__ import annotations

import re
from typing import Any

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PARAM_TYPES = ("string", "integer", "number", "boolean")
OPERATORS = ("eq", "neq", "contains", "gt", "gte", "lt", "lte")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX_ARG_STRING = 1000


class ValidationError(Exception):
    """Raised with a dict of field -> message (or a single message)."""

    def __init__(self, errors: dict[str, str] | str):
        if isinstance(errors, str):
            errors = {"_": errors}
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" if k != "_" else v for k, v in errors.items()))


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = "action_" + slug
    return slug[:max_len].rstrip("_") or "action"


def clean_text(value: Any, field: str, errors: dict, *, min_len=0, max_len=500, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors[field] = "Must be text."
        return ""
    value = CONTROL_CHARS.sub("", value).strip()
    if required and not value:
        errors[field] = "This field is required."
    elif value and len(value) < min_len:
        errors[field] = f"Must be at least {min_len} characters."
    elif len(value) > max_len:
        errors[field] = f"Must be at most {max_len} characters."
    return value


def validate_chatbot_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Invalid request body.")
    errors: dict[str, str] = {}
    out = {
        "name": clean_text(data.get("name"), "name", errors, min_len=2, max_len=80, required=True),
        "description": clean_text(data.get("description"), "description", errors, max_len=500),
        "purpose": clean_text(data.get("purpose"), "purpose", errors, max_len=2000),
        "welcome_message": clean_text(data.get("welcome_message"), "welcome_message", errors, max_len=500),
        "system_instructions": clean_text(data.get("system_instructions"), "system_instructions", errors, max_len=4000),
    }
    enabled = data.get("enabled_actions", [])
    if not isinstance(enabled, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in enabled):
        errors["enabled_actions"] = "Must be a list of action ids."
        enabled = []
    out["enabled_actions"] = sorted(set(enabled))
    try:
        temp = float(data.get("temperature", 0.2))
        if not 0 <= temp <= 1:
            raise ValueError
    except (TypeError, ValueError):
        errors["temperature"] = "Must be a number between 0 and 1."
        temp = 0.2
    out["temperature"] = temp
    out["model_override"] = clean_text(data.get("model_override"), "model_override", errors, max_len=100)
    out["test_mode_default"] = 1 if data.get("test_mode_default", True) else 0
    if errors:
        raise ValidationError(errors)
    return out


# --------------------------------------------------------------------------- runtime argument validation


def coerce_value(param: dict, raw: Any) -> Any:
    """Coerce one argument to the parameter's declared type or raise ValueError(message)."""
    ptype = param.get("param_type", "string")
    if ptype == "string":
        if isinstance(raw, (dict, list)):
            raise ValueError("must be text")
        val = CONTROL_CHARS.sub("", str(raw)).strip()
        if len(val) > MAX_ARG_STRING:
            raise ValueError(f"must be at most {MAX_ARG_STRING} characters")
    elif ptype == "integer":
        if isinstance(raw, bool):
            raise ValueError("must be a whole number")
        try:
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError
            val = int(str(raw).strip()) if not isinstance(raw, (int, float)) else int(raw)
        except ValueError:
            raise ValueError("must be a whole number") from None
    elif ptype == "number":
        if isinstance(raw, bool):
            raise ValueError("must be a number")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise ValueError("must be a number") from None
        if val != val or val in (float("inf"), float("-inf")):
            raise ValueError("must be a finite number")
    elif ptype == "boolean":
        if isinstance(raw, bool):
            val = raw
        elif str(raw).strip().lower() in {"true", "yes", "1"}:
            val = True
        elif str(raw).strip().lower() in {"false", "no", "0"}:
            val = False
        else:
            raise ValueError("must be true or false")
    else:
        raise ValueError("unsupported parameter type")
    choices = (param.get("extra") or {}).get("choices")
    if choices and ptype == "string" and val.lower() not in [str(c).lower() for c in choices]:
        raise ValueError("must be one of: " + ", ".join(map(str, choices)))
    if choices and ptype == "string":
        val = next(str(c) for c in choices if str(c).lower() == val.lower())
    return val


def coerce_args(params: list[dict], raw_args: Any) -> tuple[dict, list[str], dict[str, str]]:
    """Validate arguments against parameter definitions.

    Returns (clean_args, missing_required, errors). Unknown arguments are dropped.
    """
    if not isinstance(raw_args, dict):
        return {}, [], {"_": "Arguments must be an object."}
    clean: dict[str, Any] = {}
    missing: list[str] = []
    errors: dict[str, str] = {}
    for p in params:
        name = p["name"]
        raw = raw_args.get(name)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if p.get("required"):
                missing.append(name)
            continue
        try:
            clean[name] = coerce_value(p, raw)
        except ValueError as exc:
            errors[name] = f"'{p.get('label') or name}' {exc}"
    return clean, missing, errors


def render_template_text(template: str, values: dict[str, Any]) -> str:
    """Replace {name} placeholders with values; unknown placeholders are left untouched."""

    def sub(match: re.Match) -> str:
        key = match.group(1)
        return str(values[key]) if key in values else match.group(0)

    return re.sub(r"\{([a-z][a-z0-9_]*)\}", sub, template)
