"""Controlled execution of approved actions.

The LLM never supplies SQL, code, URLs or credentials: it only supplies *argument values*, which are
validated against the stored parameter definitions and then bound into fixed, parameterized runners.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from flask import current_app

from ..db import dumps, execute, loads
from ..integrations.databases import DatabaseError
from ..security import UnsafeURLError, safe_request
from ..services import actions as action_svc
from ..services import connections as conn_svc
from ..validation import coerce_args, render_template_text
from . import python_runner

MAX_RESULT_CHARS = 6000


@dataclass
class ActionResult:
    ok: bool
    summary: str = ""
    data: Any = None
    error: str = ""
    simulated: bool = False
    missing: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | error | simulated | rejected | invalid

    def for_llm(self) -> str:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.simulated:
            payload["note"] = "TEST MODE: no real change was made."
        if self.missing:
            payload["missing_required_fields"] = self.missing
            payload["instruction"] = "Ask the user for the missing information, then call the action again."
        if self.error:
            payload["error"] = self.error
        if self.data is not None:
            payload["result"] = self.data
        text = dumps(payload)
        if len(text) > MAX_RESULT_CHARS:
            payload["result"] = "(result too large; showing first part) " + dumps(self.data)[: MAX_RESULT_CHARS - 300]
            text = dumps(payload)
        return text

    def to_ui(self) -> dict:
        return {"ok": self.ok, "status": self.status, "summary": self.summary or self.error, "data": self.data,
                "error": self.error, "simulated": self.simulated, "missing": self.missing}


def _err(message: str, status="error", **kw) -> ActionResult:
    return ActionResult(False, summary=message, error=message, status=status, **kw)


def _rows_summary(n: int, noun="record") -> str:
    return f"Found {n} {noun}{'' if n == 1 else 's'}."


# --------------------------------------------------------------------------- runners


def _run_db(action: dict, args: dict, test_mode: bool) -> ActionResult:
    from ..db import q_one

    row = conn_svc.get_connection_row(action["user_id"], action["connection_id"]) if action["connection_id"] else None
    if not row:
        return _err("The database connection for this action no longer exists.")
    adapter = conn_svc.adapter_for(row)
    cfg, t = action["config"], action["template_type"]
    params = {p["name"]: p for p in action["parameters"]}
    outputs = cfg.get("output_fields") or None

    if t == "db_search":
        filters = [{"column": params[n]["maps_to"], "op": params[n]["extra"].get("operator", "eq"), "value": v} for n, v in args.items()]
        rows = adapter.select(cfg["table"], outputs, filters, cfg.get("limit", 10))
        return ActionResult(True, _rows_summary(len(rows)), rows)
    if t == "db_get_by_id":
        rec = adapter.get_by_id(cfg["table"], cfg["id_column"], args["id"], outputs)
        if rec is None:
            return ActionResult(True, "No record found with that ID.", None)
        return ActionResult(True, "Found 1 record.", rec)
    if t == "db_insert":
        values = {params[n]["maps_to"]: v for n, v in args.items()}
        res = adapter.insert(cfg["table"], values, dry_run=test_mode)
        return ActionResult(True, "Test mode: record validated but NOT saved." if test_mode else "Record created.", res, simulated=test_mode, status="simulated" if test_mode else "ok")
    if t == "db_update":
        key = args.pop("id")
        values = {params[n]["maps_to"]: v for n, v in args.items()}
        if not values:
            return _err("Nothing to update: provide at least one field to change.", status="invalid")
        res = adapter.update(cfg["table"], cfg["id_column"], key, values, dry_run=test_mode)
        if not test_mode and res.get("rows_affected") == 0:
            return ActionResult(True, "No record matched that ID, so nothing was changed.", res)
        return ActionResult(True, "Test mode: update validated but NOT saved." if test_mode else "Record updated.", res, simulated=test_mode, status="simulated" if test_mode else "ok")
    return _err("Unsupported database action.")


def _extract_path(data: Any, path: str) -> Any:
    cur = data
    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _run_rest(action: dict, args: dict, test_mode: bool) -> ActionResult:
    cfg = action["config"]
    method = cfg["method"]
    if test_mode and method != "GET":
        return ActionResult(True, f"Test mode: {method} request validated but NOT sent.", {"would_send": {"method": method, "url": cfg["url"], "values": args}},
                            simulated=True, status="simulated")
    url, query, body = cfg["url"], {}, {}
    for p in action["parameters"]:
        if p["name"] not in args:
            continue
        loc, key = p["extra"].get("location", "query"), p["maps_to"] or p["name"]
        if loc == "path":
            url = url.replace("{" + p["name"] + "}", quote(str(args[p["name"]]), safe=""))
        elif loc == "body":
            body[key] = args[p["name"]]
        else:
            query[key] = args[p["name"]]
    headers = {"Accept": "application/json", "User-Agent": "NoCodeBotBuilder/1.0"}
    if cfg.get("auth_header"):
        headers[cfg["auth_header"]] = action_svc.secret_values(action).get("auth_value", "")
    cfgapp = current_app.config
    resp = safe_request(
        method, url, params=query or None, json_body=body or None if method in ("POST", "PUT", "PATCH") else None,
        headers=headers, timeout=min(float(cfg.get("timeout", 10)), cfgapp["API_TIMEOUT_SECONDS"] * 3),
        max_bytes=cfgapp["API_MAX_BYTES"], allow_private=cfgapp["ALLOW_PRIVATE_API_HOSTS"],
        allowed_hosts=cfgapp["ALLOWED_API_HOSTS"],
    )
    if resp.status >= 400:
        return _err(f"The API responded with an error (HTTP {resp.status}).")
    try:
        payload = resp.json()
    except ValueError:
        payload = resp.text[:2000]
    paths = cfg.get("output_fields") or []
    data = {p: _extract_path(payload, p) for p in paths} if paths and not isinstance(payload, str) else payload
    return ActionResult(True, f"The API responded (HTTP {resp.status}).", data)


def _run_static(action: dict, args: dict, _test: bool) -> ActionResult:
    text = render_template_text(action["config"]["response_text"], args)
    return ActionResult(True, "Predefined response.", {"response": text})


def _run_custom(action: dict, args: dict, test_mode: bool) -> ActionResult:
    cfg = action["config"]
    values = dict(args)
    result: dict = {}
    if cfg.get("python_code"):
        if not current_app.config["ENABLE_PYTHON_ACTIONS"]:
            return _err("Python actions are disabled on this server.")
        result = python_runner.run_code(cfg["python_code"], args, current_app.config["PYTHON_ACTION_TIMEOUT"])
        values.update({k: v for k, v in result.items() if re.fullmatch(r"[a-z][a-z0-9_]*", k)})
    data: dict = {"result": result} if result else {}
    if cfg.get("result_template"):
        data["response"] = render_template_text(cfg["result_template"], values)
    return ActionResult(True, "Custom action completed.", data or result)


RUNNERS = {
    "db_search": _run_db, "db_get_by_id": _run_db, "db_insert": _run_db, "db_update": _run_db,
    "rest_api": _run_rest, "static_response": _run_static, "custom_form": _run_custom,
}


# --------------------------------------------------------------------------- public API


def log_execution(action: dict | None, name: str, args: dict, result: ActionResult, *, user_id: int, chatbot_id=None,
                  conversation_id=None, test_mode=False, source="chat", duration_ms=0, status=None) -> None:
    execute(
        "INSERT INTO action_execution_logs (user_id, chatbot_id, conversation_id, action_id, action_name, arguments, status, "
        "result_summary, error, test_mode, source, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, chatbot_id, conversation_id, action["id"] if action else None, name, dumps(args)[:2000],
         status or result.status, (result.summary or "")[:500], result.error[:500], 1 if test_mode else 0, source, duration_ms),
    )


def execute_action(action: dict, raw_args: Any, *, user_id: int, test_mode: bool, chatbot_id: int | None = None,
                   conversation_id: int | None = None, source: str = "chat") -> ActionResult:
    """Validate and run one action. Always returns a result (never raises) and always logs."""
    started = time.monotonic()
    args: dict = {}
    try:
        if action["user_id"] != user_id:  # defence in depth
            result = _err("Not allowed.", status="rejected")
        elif not action["enabled"]:
            result = _err("This action is disabled.", status="rejected")
        elif action["is_write"] and not action["approved"] and not (
                test_mode and source == "manual_test" and action["template_type"] != "custom_form"):
            # (a manual dry-run of a write action is harmless, so it may run before approval; code actions never may)
            result = _err("This action changes data and has not been approved yet. Approve it on the Custom Actions page.", status="rejected")
        else:
            args, missing, errors = coerce_args(action["parameters"], raw_args)
            if errors:
                result = _err("; ".join(errors.values()), status="invalid")
            elif missing:
                labels = [next(p["label"] for p in action["parameters"] if p["name"] == m) for m in missing]
                result = ActionResult(False, "Missing information: " + ", ".join(labels), error="Missing required fields",
                                      missing=missing, status="invalid")
            else:
                try:
                    result = RUNNERS[action["template_type"]](action, dict(args), test_mode)
                except DatabaseError as exc:
                    result = _err(str(exc))
                except UnsafeURLError as exc:
                    result = _err(f"Blocked for safety: {exc}", status="rejected")
                except (TimeoutError, ConnectionError, RuntimeError, python_runner.CodeRejected) as exc:
                    result = _err(str(exc))
                except Exception:  # noqa: BLE001 - never leak internals
                    current_app.logger.exception("Action %s crashed", action["tool_name"])
                    result = _err("Something went wrong while running this action.")
    finally:
        pass
    duration = int((time.monotonic() - started) * 1000)
    log_execution(action, action["tool_name"], args or (raw_args if isinstance(raw_args, dict) else {}), result,
                  user_id=user_id, chatbot_id=chatbot_id, conversation_id=conversation_id, test_mode=test_mode,
                  source=source, duration_ms=duration)
    return result
