from __future__ import annotations

from flask import current_app

from ..security import UnsafeURLError, safe_request
from .base import LLMError


def post_json(url: str, headers: dict, payload: dict, timeout: float = 60) -> dict:
    allow_private = current_app.config["ALLOW_PRIVATE_LLM_URLS"]
    try:
        resp = safe_request("POST", url, json_body=payload, headers={**headers, "Content-Type": "application/json"},
                            timeout=timeout, max_bytes=4 * 1024 * 1024, allow_private=allow_private)
    except UnsafeURLError as exc:
        raise LLMError(f"The LLM address was rejected: {exc}") from exc
    except TimeoutError as exc:
        raise LLMError("The AI service took too long to respond. Please try again.") from exc
    except ConnectionError as exc:
        raise LLMError(f"Could not reach the AI service. {exc}") from exc
    if resp.status in (401, 403):
        raise LLMError("The AI service rejected the API key. Check it on the Settings page.")
    if resp.status == 429:
        raise LLMError("The AI service is rate limiting requests. Please wait a moment and try again.")
    if resp.status >= 400:
        detail = ""
        try:
            err = resp.json().get("error", {})
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except Exception:  # noqa: BLE001
            pass
        raise LLMError(f"The AI service returned an error (HTTP {resp.status}). {detail[:200]}".strip())
    try:
        return resp.json()
    except ValueError as exc:
        raise LLMError("The AI service returned an unreadable response.") from exc
