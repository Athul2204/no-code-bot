"""Configuration: environment variables (.env) -> Flask config dict."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _persisted_secret(path: Path, factory) -> str:
    """Return a secret stored in a private file, creating it on first use."""
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = factory()
    path.write_text(value)
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows
        pass
    return value


def _parse_rate(raw: str, default=(30, 60)) -> tuple[int, int]:
    try:
        count, window = raw.split("/")
        return int(count), int(window)
    except (ValueError, AttributeError):
        return default


def load_config(overrides: dict | None = None) -> dict:
    instance_dir = Path(os.environ.get("INSTANCE_DIR", BASE_DIR / "instance"))
    cfg = {
        "BASE_DIR": str(BASE_DIR),
        "INSTANCE_DIR": str(instance_dir),
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", str(instance_dir / "app.db")),
        "USER_DB_DIR": os.environ.get("USER_DB_DIR", str(BASE_DIR / "data" / "user_databases")),
        "EXPORT_DIR": os.environ.get("EXPORT_DIR", str(BASE_DIR / "exports")),
        "SAMPLES_DIR": str(BASE_DIR / "samples"),
        "SECRET_KEY": os.environ.get("SECRET_KEY") or "",
        "ENCRYPTION_KEY": os.environ.get("ENCRYPTION_KEY") or "",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": _bool("SESSION_COOKIE_SECURE", False),
        "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
        "ALLOW_REGISTRATION": _bool("ALLOW_REGISTRATION", True),
        "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "anthropic").strip().lower(),
        "LLM_API_KEY": os.environ.get("LLM_API_KEY", ""),
        "LLM_MODEL": os.environ.get("LLM_MODEL", ""),
        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", ""),
        "ALLOW_PRIVATE_LLM_URLS": _bool("ALLOW_PRIVATE_LLM_URLS", False),
        "ALLOWED_API_HOSTS": [h.strip().lower() for h in os.environ.get("ALLOWED_API_HOSTS", "").split(",") if h.strip()],
        "ALLOW_PRIVATE_API_HOSTS": _bool("ALLOW_PRIVATE_API_HOSTS", False),
        "API_TIMEOUT_SECONDS": _int("API_TIMEOUT_SECONDS", 10),
        "API_MAX_BYTES": 1024 * 1024,
        "ENABLE_PYTHON_ACTIONS": _bool("ENABLE_PYTHON_ACTIONS", False),
        "PYTHON_ACTION_TIMEOUT": 5,
        "CHAT_RATE_LIMIT": _parse_rate(os.environ.get("CHAT_RATE_LIMIT", "30/60")),
        "MAX_TOOL_ROUNDS": _int("MAX_TOOL_ROUNDS", 5),
        "HISTORY_LIMIT": 40,
        "RASA_ENABLED": _bool("RASA_ENABLED", True),
        "TESTING": False,
    }
    if overrides:
        cfg.update(overrides)
    inst = Path(cfg["INSTANCE_DIR"])
    if not cfg["SECRET_KEY"]:
        cfg["SECRET_KEY"] = _persisted_secret(inst / "secret_key", lambda: secrets.token_hex(32))
    if not cfg["ENCRYPTION_KEY"]:
        cfg["ENCRYPTION_KEY"] = _persisted_secret(inst / "encryption_key", lambda: Fernet.generate_key().decode())
    return cfg
