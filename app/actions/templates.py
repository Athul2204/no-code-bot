"""Action templates: the only way executable behaviour is created.

An action is *configuration* (JSON validated here) interpreted by fixed, reviewed runner code in
executor.py. Nothing the user types is ever turned into code, except the optional, separately
sandboxed Python template.
"""
from __future__ import annotations

import re
from typing import Any

from flask import current_app

from ..security import UnsafeURLError, validate_url_syntax
from ..validation import (OPERATORS, PARAM_NAME_RE, PARAM_TYPES, ValidationError, clean_text, slugify)
from . import python_runner

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "cookie"}
PATH_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")

TEMPLATES: dict[str, dict] = {
    "db_search": {
        "label": "Search database records", "icon": "search", "needs_connection": True,
        "description": "Find rows in a table using filters the chatbot fills in (for example, products under a price).",
    },
    "db_get_by_id": {
        "label": "Retrieve a record by ID", "icon": "id", "needs_connection": True,
        "description": "Look up exactly one row by its ID (for example, an order number).",
    },
    "db_insert": {
        "label": "Insert a record", "icon": "plus", "needs_connection": True,
        "description": "Add a new row to a table. Needs explicit approval and a write-enabled connection.",
    },
    "db_update": {
        "label": "Update a record", "icon": "edit", "needs_connection": True,
        "description": "Change fields on an existing row. Needs explicit approval and a write-enabled connection.",
    },
    "rest_api": {
        "label": "Call an external REST API", "icon": "globe", "needs_connection": False,
        "description": "Call a public web API. Requests are time-limited and blocked from private networks.",
    },
    "static_response": {
        "label": "Send a predefined response", "icon": "message", "needs_connection": False,
        "description": "Reply with fixed text, optionally filled with values from the conversation.",
    },
    "custom_form": {
        "label": "Custom action (form)", "icon": "wrench", "needs_connection": False,
        "description": "Define your own inputs and a response template. Optional advanced Python runs in an isolated process.",
    },
}


def template_catalog() -> list[dict]:
    return [{"type": k, **{f: v[f] for f in ("label", "icon", "description", "needs_connection")}} for k, v in TEMPLATES.items()]


def is_write_action(template_type: str, config: dict) -> bool:
    if template_type in ("db_insert", "db_update"):
        return True
    if template_type == "rest_api":
        return config.get("method", "GET") in WRITE_METHODS
    if template_type == "custom_form":
        return bool(config.get("python_code"))
    return False


# --------------------------------------------------------------------------- parameter helpers


def _clean_params(raw: Any, errors: dict, *, template: str, columns: dict[str, dict] | None = None,
                  min_count=0, max_count=15) -> list[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        errors["parameters"] = "Parameters must be a list."
        return []
    if len(raw) > max_count:
        errors["parameters"] = f"At most {max_count} parameters are allowed."
        return []
    out, seen = [], set()
    for i, p in enumerate(raw):
        key = f"parameters.{i}"
        if not isinstance(p, dict):
            errors[key] = "Invalid parameter."
            continue
        local: dict[str, str] = {}
        label = clean_text(p.get("label"), "label", local, max_len=60)
        maps_to = clean_text(p.get("maps_to"), "maps_to", local, max_len=80)
        name = clean_text(p.get("name"), "name", local, max_len=40).lower()
        if not name:
            name = slugify(label or maps_to or f"field_{i + 1}", 40)
        if not PARAM_NAME_RE.match(name):
            local["name"] = "Use lowercase letters, numbers and underscores, starting with a letter."
        if name in seen:
            local["name"] = f"Duplicate parameter name '{name}'."
        seen.add(name)
        ptype = p.get("param_type") or "string"
        if ptype not in PARAM_TYPES:
            local["param_type"] = "Choose text, whole number, number or yes/no."
        description = clean_text(p.get("description"), "description", local, max_len=200)
        extra_in = p.get("extra") if isinstance(p.get("extra"), dict) else {}
        extra: dict[str, Any] = {}
        if columns is not None:
            if maps_to not in columns:
                local["maps_to"] = "Choose a column from the table."
        if template == "db_search":
            op = extra_in.get("operator", "eq")
            if op not in OPERATORS:
                local["operator"] = "Unknown comparison."
            extra["operator"] = op
        if template == "rest_api":
            loc = extra_in.get("location", "query")
            if loc not in ("path", "query", "body"):
                local["location"] = "Choose path, query or body."
            extra["location"] = loc
            if not maps_to:
                maps_to = name
        choices = extra_in.get("choices")
        if choices:
            if not (isinstance(choices, list) and len(choices) <= 20 and all(isinstance(c, str) and 0 < len(c) <= 60 for c in choices)):
                local["choices"] = "Choices must be a short list of text values."
            elif ptype == "string":
                extra["choices"] = choices
        if extra_in.get("role") == "key":
            extra["role"] = "key"
        for field, msg in local.items():
            errors[f"{key}.{field}"] = msg
        out.append({
            "name": name, "label": label or name.replace("_", " ").title(), "param_type": ptype if ptype in PARAM_TYPES else "string",
            "description": description, "required": bool(p.get("required")), "maps_to": maps_to, "extra": extra,
        })
    if len(out) < min_count and "parameters" not in errors:
        errors["parameters"] = f"Add at least {min_count} input field(s)."
    return out


def _column_param_type(col: dict) -> str:
    t = (col.get("type") or "").upper()
    if "INT" in t:
        return "integer"
    if any(x in t for x in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "number"
    return "string"


def _output_fields(raw: Any, errors: dict, columns: dict[str, dict]) -> list[str]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        errors["output_fields"] = "Choose output fields from the list."
        return []
    for c in raw:
        if c not in columns:
            errors["output_fields"] = f"'{c}' is not a column of this table."
    return raw[:30]


def _pick_table(config: dict, errors: dict, schema: list[dict]) -> tuple[str, dict[str, dict]]:
    table = config.get("table")
    tables = {t["name"]: {c["name"]: c for c in t["columns"]} for t in schema}
    if table not in tables:
        errors["table"] = "Choose a table."
        return "", {}
    return table, tables[table]


# --------------------------------------------------------------------------- per-template validation
# Each returns (clean_config, clean_parameters). `schema` = [{name, columns:[...]}] for DB templates.


def _v_db_search(cfg, params, schema, errors):
    table, cols = _pick_table(cfg, errors, schema)
    clean = {"table": table, "output_fields": _output_fields(cfg.get("output_fields"), errors, cols) if cols else []}
    try:
        limit = int(cfg.get("limit", 10))
        if not 1 <= limit <= 50:
            raise ValueError
    except (TypeError, ValueError):
        errors["limit"] = "Choose a number from 1 to 50."
        limit = 10
    clean["limit"] = limit
    return clean, _clean_params(params, errors, template="db_search", columns=cols or {}, min_count=1)


def _v_db_get_by_id(cfg, params, schema, errors):
    table, cols = _pick_table(cfg, errors, schema)
    id_col = cfg.get("id_column")
    if cols and id_col not in cols:
        errors["id_column"] = "Choose the ID column."
    clean = {"table": table, "id_column": id_col or "",
             "output_fields": _output_fields(cfg.get("output_fields"), errors, cols) if cols else []}
    desc = clean_text(cfg.get("id_description"), "id_description", errors, max_len=200)
    clean["id_description"] = desc
    key = []
    if cols and id_col in cols:
        key = [{"name": "id", "label": "ID", "param_type": _column_param_type(cols[id_col]),
                "description": desc or f"The {id_col} of the record to retrieve", "required": True,
                "maps_to": id_col, "extra": {"role": "key"}}]
    return clean, key


def _v_db_insert(cfg, params, schema, errors):
    table, cols = _pick_table(cfg, errors, schema)
    clean = {"table": table}
    plist = _clean_params(params, errors, template="db_insert", columns=cols or {}, min_count=1)
    if cols and not errors:
        mapped = {p["maps_to"] for p in plist}
        for c in cols.values():
            if c["notnull"] and c["default"] is None and not c["pk"] and c["name"] not in mapped:
                errors["parameters"] = f"The table requires a value for '{c['name']}'. Add an input field for it."
                break
    for p in plist:
        if cols.get(p["maps_to"], {}).get("notnull") and not cols[p["maps_to"]]["pk"] and cols[p["maps_to"]]["default"] is None:
            p["required"] = True
    return clean, plist


def _v_db_update(cfg, params, schema, errors):
    table, cols = _pick_table(cfg, errors, schema)
    id_col = cfg.get("id_column")
    if cols and id_col not in cols:
        errors["id_column"] = "Choose the ID column."
    clean = {"table": table, "id_column": id_col or ""}
    plist = _clean_params(params, errors, template="db_update", columns=cols or {}, min_count=1)
    for p in plist:
        if p["name"] == "id":
            errors["parameters"] = "'id' is reserved for the record ID. Use a different field name."
        if p["maps_to"] == id_col:
            errors["parameters"] = "The ID column cannot be updated."
        p["required"] = False  # optional fields: only supplied ones are changed
    key = []
    if cols and id_col in cols:
        key = [{"name": "id", "label": "ID", "param_type": _column_param_type(cols[id_col]),
                "description": f"The {id_col} of the record to update", "required": True,
                "maps_to": id_col, "extra": {"role": "key"}}]
    return clean, key + plist


def _v_rest_api(cfg, params, schema, errors):
    url = clean_text(cfg.get("url"), "url", errors, max_len=500, required=True)
    if url:
        try:
            validate_url_syntax(url)
        except UnsafeURLError as exc:
            errors["url"] = str(exc)
    method = (cfg.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        errors["method"] = "Unsupported method."
    try:
        timeout = float(cfg.get("timeout", 10))
        if not 1 <= timeout <= 30:
            raise ValueError
    except (TypeError, ValueError):
        errors["timeout"] = "Choose between 1 and 30 seconds."
        timeout = 10
    paths = cfg.get("output_fields") or []
    if not (isinstance(paths, list) and len(paths) <= 20 and all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,80}", x) for x in paths)):
        errors["output_fields"] = "Use field paths like data.name or items.0.title."
        paths = []
    header = clean_text(cfg.get("auth_header"), "auth_header", errors, max_len=40)
    if header and (not re.fullmatch(r"[A-Za-z0-9-]{1,40}", header) or header.lower() in FORBIDDEN_HEADERS):
        errors["auth_header"] = "Not a valid header name."
    plist = _clean_params(params, errors, template="rest_api", min_count=0)
    placeholders = set(PATH_PLACEHOLDER.findall(url or ""))
    path_params = {p["name"] for p in plist if p["extra"].get("location") == "path"}
    if placeholders - path_params:
        errors["parameters"] = "Every {placeholder} in the URL needs an input field with location 'path': " + ", ".join(sorted(placeholders - path_params))
    for p in plist:
        if p["extra"].get("location") == "path" and p["name"] not in placeholders:
            errors["parameters"] = f"Input '{p['name']}' is marked as path but the URL has no {{{p['name']}}}."
        if p["extra"].get("location") == "body" and method not in ("POST", "PUT", "PATCH"):
            errors["parameters"] = "Body fields can only be used with POST, PUT or PATCH."
        if p["extra"].get("location") == "path":
            p["required"] = True
    return {"url": url, "method": method, "timeout": timeout, "output_fields": paths, "auth_header": header}, plist


def _v_static(cfg, params, schema, errors):
    text = clean_text(cfg.get("response_text"), "response_text", errors, max_len=2000, required=True)
    plist = _clean_params(params, errors, template="static_response", max_count=10)
    unknown = set(PATH_PLACEHOLDER.findall(text)) - {p["name"] for p in plist}
    if unknown:
        errors["response_text"] = "Unknown placeholder(s): " + ", ".join("{%s}" % u for u in sorted(unknown)) + ". Add matching input fields."
    return {"response_text": text}, plist


def _v_custom(cfg, params, schema, errors):
    plist = _clean_params(params, errors, template="custom_form", max_count=10)
    template = clean_text(cfg.get("result_template"), "result_template", errors, max_len=2000)
    code = cfg.get("python_code") or ""
    clean = {"result_template": template, "python_code": ""}
    if code.strip():
        if not current_app.config.get("ENABLE_PYTHON_ACTIONS"):
            errors["python_code"] = "Python actions are disabled on this server (set ENABLE_PYTHON_ACTIONS=true to allow them)."
        else:
            try:
                python_runner.validate_code(code)
                clean["python_code"] = code
            except python_runner.CodeRejected as exc:
                errors["python_code"] = str(exc)
    if not template and not code.strip():
        errors["result_template"] = "Write a response template (or add advanced Python code)."
    names = {p["name"] for p in plist}
    if template and not code.strip():
        unknown = set(PATH_PLACEHOLDER.findall(template)) - names
        if unknown:
            errors["result_template"] = "Unknown placeholder(s): " + ", ".join("{%s}" % u for u in sorted(unknown))
    return clean, plist


VALIDATORS = {
    "db_search": _v_db_search, "db_get_by_id": _v_db_get_by_id, "db_insert": _v_db_insert,
    "db_update": _v_db_update, "rest_api": _v_rest_api, "static_response": _v_static, "custom_form": _v_custom,
}


def validate_action_config(template_type: str, config: Any, parameters: Any, schema: list[dict] | None) -> tuple[dict, list[dict]]:
    if template_type not in VALIDATORS:
        raise ValidationError({"template_type": "Unknown action type."})
    if not isinstance(config, dict):
        raise ValidationError({"config": "Invalid configuration."})
    errors: dict[str, str] = {}
    clean_cfg, clean_params = VALIDATORS[template_type](config, parameters, schema or [], errors)
    if errors:
        raise ValidationError(errors)
    return clean_cfg, clean_params
