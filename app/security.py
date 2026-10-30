"""Security helpers: encryption, CSRF, auth, rate limiting, SSRF-safe HTTP."""
from __future__ import annotations

import hmac
import ipaddress
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from requests.adapters import HTTPAdapter

from .db import q_one

# --------------------------------------------------------------------------- encryption


def _fernet() -> Fernet:
    return Fernet(current_app.config["ENCRYPTION_KEY"].encode())


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Stored secret could not be decrypted (was the encryption key changed?).") from exc


# --------------------------------------------------------------------------- CSRF

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def verify_csrf():
    if request.method in SAFE_METHODS:
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf_token") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        if request.path.startswith("/api/"):
            resp = jsonify({"error": "Security token missing or expired. Please refresh the page."})
            resp.status_code = 403
            return resp
        abort(403)
    return None


# --------------------------------------------------------------------------- auth


def current_user() -> dict | None:
    if "user" in g:
        return g.user
    uid = session.get("user_id")
    g.user = q_one("SELECT id, email, created_at FROM users WHERE id = ?", (uid,)) if uid else None
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in."}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------- rate limiting


class RateLimiter:
    """Small in-memory sliding-window limiter (per process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                return False, max(1, int(window - (now - hits[0])))
            hits.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limited(scope: str):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            limit, window = current_app.config["CHAT_RATE_LIMIT"]
            user = current_user()
            key = f"{scope}:{user['id'] if user else request.remote_addr}"
            ok, retry = limiter.allow(key, limit, window)
            if not ok:
                resp = jsonify({"error": f"Too many messages. Please wait {retry}s and try again."})
                resp.status_code = 429
                resp.headers["Retry-After"] = str(retry)
                return resp
            return view(*args, **kwargs)

        return wrapped

    return deco


# --------------------------------------------------------------------------- SSRF-safe outbound HTTP


class UnsafeURLError(ValueError):
    pass


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def validate_url_syntax(url: str) -> "urlsplit":
    """Cheap, DNS-free checks used when a URL is saved."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is required.")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http:// and https:// URLs are allowed.")
    if not parts.hostname:
        raise UnsafeURLError("URL must include a host name.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    if "{" in (parts.netloc or ""):
        raise UnsafeURLError("The host part of the URL cannot contain placeholders.")
    return parts


def resolve_safe_host(host: str, port: int, *, allow_private: bool, allowed_hosts: list[str] | None) -> str:
    """Resolve host and return one IP that passes the SSRF checks."""
    if allowed_hosts and host.lower() not in allowed_hosts:
        raise UnsafeURLError(f"Host '{host}' is not on the allowed API hosts list.")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host '{host}'.") from exc
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not ips:
        raise UnsafeURLError(f"Could not resolve host '{host}'.")
    if not allow_private:
        for ip in ips:
            if _ip_is_blocked(ip):
                raise UnsafeURLError("That address points to a private or internal network, which is not allowed.")
    return str(ips[0])


class _PinnedHTTPSAdapter(HTTPAdapter):
    """Connect to a pre-validated IP while verifying the certificate for the real hostname."""

    def __init__(self, hostname: str, **kw):
        self._hostname = hostname
        super().__init__(**kw)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["server_hostname"] = self._hostname
        pool_kwargs["assert_hostname"] = self._hostname
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class HTTPResult:
    def __init__(self, status: int, headers: dict, body: bytes, truncated: bool):
        self.status = status
        self.headers = headers
        self.body = body
        self.truncated = truncated

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        import json as _json

        return _json.loads(self.text)


def safe_request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body=None,
    headers: dict | None = None,
    timeout: float = 10,
    max_bytes: int = 1024 * 1024,
    allow_private: bool = False,
    allowed_hosts: list[str] | None = None,
) -> HTTPResult:
    """HTTP request with SSRF protection: scheme/host validation, DNS pinning, no redirects, size cap."""
    parts = validate_url_syntax(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = resolve_safe_host(parts.hostname, port, allow_private=allow_private, allowed_hosts=allowed_hosts)
    host_for_url = f"[{ip}]" if ":" in ip else ip
    netloc = f"{host_for_url}:{port}"
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    hdrs = dict(headers or {})
    hdrs["Host"] = parts.netloc
    session_ = requests.Session()
    session_.trust_env = False  # ignore proxy env so pinning is honoured
    if parts.scheme == "https":
        session_.mount("https://", _PinnedHTTPSAdapter(parts.hostname))
    try:
        resp = session_.request(
            method.upper(), pinned, params=params, json=json_body, headers=hdrs,
            timeout=timeout, allow_redirects=False, stream=True,
        )
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("The request timed out.") from exc
    except requests.exceptions.RequestException as exc:
        raise ConnectionError(f"The request failed: {exc.__class__.__name__}") from exc
    try:
        if 300 <= resp.status_code < 400:
            raise ConnectionError("The server tried to redirect the request; redirects are not followed.")
        chunks, size, truncated = [], 0, False
        for chunk in resp.iter_content(chunk_size=8192):
            size += len(chunk)
            if size > max_bytes:
                chunks.append(chunk[: max_bytes - (size - len(chunk))])
                truncated = True
                break
            chunks.append(chunk)
        return HTTPResult(resp.status_code, dict(resp.headers), b"".join(chunks), truncated)
    finally:
        resp.close()
        session_.close()


# --------------------------------------------------------------------------- headers


def apply_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp
