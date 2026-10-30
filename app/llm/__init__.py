"""LLM provider factory. Keys come from the user's encrypted settings, falling back to .env."""
from __future__ import annotations

from flask import current_app

from ..db import execute, q_all, q_one
from ..security import decrypt_text, encrypt_text
from .base import LLMError, LLMProvider, LLMResponse, ToolCall
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider

PROVIDERS = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}
SETTING_KEYS = ("llm_provider", "llm_model", "llm_base_url", "llm_api_key")

SETUP_MESSAGE = (
    "The AI model is not set up yet. Open Settings and choose a provider and paste your API key "
    "(or set LLM_API_KEY in the .env file). You can still try your configured actions without the AI "
    "using the 'Test actions' panel."
)


def get_user_setting(user_id: int, key: str) -> str:
    row = q_one("SELECT value, is_secret FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
    if not row:
        return ""
    return decrypt_text(row["value"]) if row["is_secret"] else row["value"]


def set_user_setting(user_id: int, key: str, value: str, secret: bool = False) -> None:
    if value == "":
        execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        return
    stored = encrypt_text(value) if secret else value
    execute(
        "INSERT INTO user_settings (user_id, key, value, is_secret) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, is_secret=excluded.is_secret, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        (user_id, key, stored, 1 if secret else 0),
    )


def effective_settings(user_id: int) -> dict:
    cfg = current_app.config
    try:
        key = get_user_setting(user_id, "llm_api_key")
    except ValueError:
        key = ""
    return {
        "provider": get_user_setting(user_id, "llm_provider") or cfg["LLM_PROVIDER"] or "anthropic",
        "model": get_user_setting(user_id, "llm_model") or cfg["LLM_MODEL"],
        "base_url": get_user_setting(user_id, "llm_base_url") or cfg["LLM_BASE_URL"],
        "api_key": key or cfg["LLM_API_KEY"],
        "key_source": "settings" if key else ("env" if cfg["LLM_API_KEY"] else "none"),
    }


def get_provider(user_id: int) -> LLMProvider | None:
    """Return a configured provider or None when credentials are missing."""
    override = current_app.extensions.get("llm_provider_override")
    if override is not None:  # used by tests
        return override
    s = effective_settings(user_id)
    cls = PROVIDERS.get(s["provider"])
    if cls is None:
        return None
    if not s["api_key"] and not (s["provider"] == "openai" and s["base_url"]):
        return None  # OpenAI-compatible local servers (Ollama) may run without a key
    return cls(s["api_key"], s["model"], s["base_url"])


__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ToolCall", "get_provider", "effective_settings",
           "get_user_setting", "set_user_setting", "SETUP_MESSAGE", "PROVIDERS"]
