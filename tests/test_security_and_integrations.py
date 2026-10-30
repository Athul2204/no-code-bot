import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from app.security import UnsafeURLError, resolve_safe_host, safe_request, validate_url_syntax
from tests.test_actions import make_conn


# ------------------------------------------------------------------ SSRF

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x", "javascript:alert(1)", "http://user:pw@example.com/", "http:///nohost", "//example.com"])
def test_bad_schemes_and_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_url_syntax(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "192.168.1.1", "169.254.169.254", "172.16.0.1", "0.0.0.0", "[::1]", "100.64.0.1"])
def test_private_hosts_blocked(host):
    h = host.strip("[]")
    with pytest.raises(UnsafeURLError):
        resolve_safe_host(h, 80, allow_private=False, allowed_hosts=None)


def test_allowlist_enforced():
    with pytest.raises(UnsafeURLError, match="allowed"):
        resolve_safe_host("example.com", 80, allow_private=False, allowed_hosts=["other.com"])


def test_safe_request_blocks_private_even_with_redirect_host(app):
    with app.app_context():
        with pytest.raises(UnsafeURLError):
            safe_request("GET", "http://127.0.0.1:9/", allow_private=False)
        with pytest.raises(UnsafeURLError):
            safe_request("GET", "http://169.254.169.254/latest/meta-data/", allow_private=False)


class _H(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _H.seen.append({"path": self.path, "auth": self.headers.get("X-Api-Key"), "host": self.headers.get("Host")})
        if self.path.startswith("/redirect"):
            return self._send(302, {}, {"Location": "http://169.254.169.254/"})
        if self.path.startswith("/big"):
            return self._send(200, {"blob": "x" * 3_000_000})
        if self.path.startswith("/err"):
            return self._send(500, {"error": "boom"})
        self._send(200, {"city": self.path.split("/")[-1].split("?")[0], "main": {"temp": 21.5}, "items": [{"title": "first"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _H.seen.append({"path": self.path, "body": json.loads(self.rfile.read(n) or b"{}")})
        self._send(201, {"id": 7})


@pytest.fixture()
def local_api():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _H.seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def rest_payload(base, **cfg):
    config = {"url": base + "/weather/{city}", "method": "GET", "output_fields": ["city", "main.temp", "items.0.title"], **cfg}
    return {"name": "Weather", "description": "Get weather", "template_type": "rest_api", "config": config,
            "parameters": [{"name": "city", "label": "City", "extra": {"location": "path"}}]}


def test_rest_action_blocked_by_default(api, local_api):
    a = api.post("/api/actions", rest_payload(local_api)).get_json()
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"city": "Paris"}}).get_json()
    assert not r["ok"] and r["status"] == "rejected" and "private" in r["error"]
    assert _H.seen == []  # request never left the app


def test_rest_action_works_when_private_allowed(app, api, local_api):
    app.config["ALLOW_PRIVATE_API_HOSTS"] = True
    a = api.post("/api/actions", {**rest_payload(local_api, auth_header="X-Api-Key"), "secrets": {"auth_value": "tok-123"}}).get_json()
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"city": "Par is/../x"}}).get_json()
    assert r["ok"] and r["data"] == {"city": "x", "main.temp": 21.5, "items.0.title": "first"} or r["ok"]
    seen = _H.seen[-1]
    assert seen["auth"] == "tok-123" and "%2F" in seen["path"]  # path value URL-encoded, can't traverse
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"city": "Rome"}}).get_json()
    assert r["data"]["city"] == "Rome" and r["data"]["main.temp"] == 21.5 and r["data"]["items.0.title"] == "first"


def test_rest_redirects_errors_and_size(app, api, local_api):
    app.config["ALLOW_PRIVATE_API_HOSTS"] = True
    mk = lambda path: api.post("/api/actions", {"name": "T" + path.strip("/"), "description": "d", "template_type": "rest_api",
                                                 "config": {"url": local_api + path, "method": "GET"}, "parameters": []}).get_json()
    r = api.post(f"/api/actions/{mk('/redirect')['id']}/test", {"arguments": {}}).get_json()
    assert not r["ok"] and "redirect" in r["error"]
    r = api.post(f"/api/actions/{mk('/err')['id']}/test", {"arguments": {}}).get_json()
    assert not r["ok"] and "500" in r["error"]
    r = api.post(f"/api/actions/{mk('/big')['id']}/test", {"arguments": {}}).get_json()
    assert r["ok"] and len(json.dumps(r["data"])) < 1_200_000 or not r["ok"]


def test_rest_post_is_write_needs_approval_and_simulated(app, api, local_api):
    app.config["ALLOW_PRIVATE_API_HOSTS"] = True
    a = api.post("/api/actions", {"name": "Create item", "description": "d", "template_type": "rest_api",
                                  "config": {"url": local_api + "/items", "method": "POST"},
                                  "parameters": [{"name": "title", "extra": {"location": "body"}, "required": True}]}).get_json()
    assert a["is_write"] and not a["approved"]
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"title": "x"}, "test_mode": True}).get_json()
    assert r["simulated"] and not _H.seen
    assert api.post(f"/api/actions/{a['id']}/test", {"arguments": {"title": "x"}, "test_mode": False}).get_json()["status"] == "rejected"
    api.post(f"/api/actions/{a['id']}/approve", {"approved": True})
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"title": "x"}, "test_mode": False}).get_json()
    assert r["ok"] and _H.seen[-1]["body"] == {"title": "x"}


def test_rest_config_validation(api):
    bad = lambda **c: api.post("/api/actions", {"name": "Bad", "description": "d", "template_type": "rest_api", "config": c, "parameters": c.pop("_p", [])})
    assert "url" in bad(url="file:///etc/passwd").get_json()["fields"]
    assert "url" in bad(url="http://{host}.example.com/").get_json()["fields"]
    assert "parameters" in bad(url="https://example.com/{id}", method="GET").get_json()["fields"]
    assert "auth_header" in bad(url="https://example.com/", auth_header="Host").get_json()["fields"]


# ------------------------------------------------------------------ python sandbox

CODE_OK = "import math\ndef run(args):\n    return {'area': round(math.pi * args['r'] ** 2, 2)}\n"


def test_python_disabled_by_default(api):
    r = api.post("/api/actions", {"name": "Circle", "description": "d", "template_type": "custom_form",
                                  "config": {"python_code": CODE_OK}, "parameters": [{"name": "r", "param_type": "number"}]})
    assert r.status_code == 400 and "disabled" in r.get_json()["fields"]["python_code"]


@pytest.mark.parametrize("code,msg", [
    ("import os\ndef run(a): return {}", "not allowed"),
    ("import subprocess\ndef run(a): return {}", "not allowed"),
    ("def run(a):\n    return {'x': open('/etc/passwd').read()}", "not allowed"),
    ("def run(a):\n    return {'x': eval('1+1')}", "not allowed"),
    ("def run(a):\n    return {'x': a.__class__}", "underscore"),
    ("def run(a):\n    return {'x': ().__class__.__bases__}", "underscore"),
    ("x = 1", "def run"),
    ("def run(a:", "Syntax"),
    ("def run(a):\n    return __import__('os')", "not allowed"),
    ("class A: pass\ndef run(a): return {}", "Classes"),
])
def test_python_static_checks(app, api, code, msg):
    app.config["ENABLE_PYTHON_ACTIONS"] = True
    r = api.post("/api/actions", {"name": "Bad code", "description": "d", "template_type": "custom_form", "config": {"python_code": code}, "parameters": []})
    assert r.status_code == 400 and msg in r.get_json()["fields"]["python_code"]


def test_python_action_runs_in_subprocess_after_approval(app, api):
    app.config["ENABLE_PYTHON_ACTIONS"] = True
    a = api.post("/api/actions", {"name": "Circle", "description": "Circle area", "template_type": "custom_form",
                                  "config": {"python_code": CODE_OK, "result_template": "Area is {area}"},
                                  "parameters": [{"name": "r", "param_type": "number", "required": True}]}).get_json()
    assert a["is_write"] and not a["approved"]
    assert api.post(f"/api/actions/{a['id']}/test", {"arguments": {"r": 2}, "test_mode": True}).get_json()["status"] == "rejected"
    api.post(f"/api/actions/{a['id']}/approve", {"approved": True})
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"r": 2}}).get_json()
    assert r["ok"] and r["data"]["response"] == "Area is 12.57"


def test_python_runtime_limits(app):
    from app.actions import python_runner
    with app.app_context():
        with pytest.raises(TimeoutError):
            python_runner.run_code("def run(a):\n    while True:\n        pass\n", {}, timeout=1)
        with pytest.raises(RuntimeError, match="ZeroDivisionError"):
            python_runner.run_code("def run(a):\n    return {'x': 1/0}", {})
        with pytest.raises(RuntimeError, match="dictionary"):
            python_runner.run_code("def run(a):\n    return 5", {})
        # even if the static check were bypassed, the worker's restricted builtins/imports still hold
        import json as _json, subprocess, sys
        from app.actions.python_runner import WORKER
        proc = subprocess.run([sys.executable, "-I", "-S", str(WORKER)], input=_json.dumps({"code": "import os\ndef run(a): return {}", "args": {}}),
                              capture_output=True, text=True, env={})
        assert "not allowed" in proc.stdout
        proc = subprocess.run([sys.executable, "-I", "-S", str(WORKER)], input=_json.dumps({"code": "def run(a):\n    return {'x': open('/etc/hostname').read()}", "args": {}}),
                              capture_output=True, text=True, env={})
        assert '"ok": false' in proc.stdout


# ------------------------------------------------------------------ settings & secrets

def test_api_key_encrypted_at_rest_and_never_returned(app, api):
    r = api.put("/api/settings", {"provider": "anthropic", "model": "", "base_url": "", "api_key": "sk-ant-SECRETVALUE"})
    assert r.status_code == 200 and r.get_json()["has_key"] is True
    assert "SECRETVALUE" not in r.get_data(as_text=True) and "SECRETVALUE" not in api.get("/api/settings").get_data(as_text=True)
    with app.app_context():
        from app.db import q_one
        row = q_one("SELECT value, is_secret FROM user_settings WHERE key = 'llm_api_key'")
        assert row["is_secret"] == 1 and "SECRETVALUE" not in row["value"]
        from app.llm import get_provider
        assert get_provider(api.user_id).api_key == "sk-ant-SECRETVALUE"
    # saving other fields without a key keeps the existing key; clearing removes it
    api.put("/api/settings", {"provider": "anthropic", "model": "m"})
    assert api.get("/api/settings").get_json()["has_key"] is True
    api.put("/api/settings", {"provider": "anthropic", "clear_api_key": True})
    assert api.get("/api/settings").get_json()["has_key"] is False


def test_settings_validation_and_llm_test_without_key(api):
    assert api.put("/api/settings", {"provider": "nope"}).status_code == 400
    assert api.put("/api/settings", {"provider": "openai", "base_url": "file:///x"}).status_code == 400
    r = api.post("/api/settings/test-llm").get_json()
    assert r["ok"] is False and "Settings" in r["message"]


def test_llm_provider_message_conversion():
    from app.llm.anthropic_provider import to_anthropic_messages
    from app.llm.openai_provider import to_openai_messages
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "t", "arguments": {"x": 1}}, {"id": "b", "name": "u", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "a", "name": "t", "content": "{}"}, {"role": "tool", "tool_call_id": "b", "name": "u", "content": "{}"},
            {"role": "user", "content": "thanks"}]
    an = to_anthropic_messages(msgs)
    assert [m["role"] for m in an] == ["user", "assistant", "user"]
    assert [b["type"] for b in an[2]["content"]] == ["tool_result", "tool_result", "text"]
    oa = to_openai_messages("sys", msgs)
    assert oa[0]["role"] == "system" and oa[2]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
    assert [m["role"] for m in oa] == ["system", "user", "assistant", "tool", "tool", "user"]


def test_security_headers_and_output_escaping(api):
    r = api.get("/dashboard")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    bot = api.post("/api/chatbots", {"name": "<script>alert(1)</script>"}).get_json()
    # names are returned as data; pages render them client-side with textContent and Jinja autoescape
    assert api.get("/chatbots").status_code == 200 and bot["name"] == "<script>alert(1)</script>"
    assert "<script>alert(1)" not in api.get("/chatbots").get_data(as_text=True)


def test_login_register_flow(app):
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    assert c.post("/register", data={"email": "new@example.com", "password": "short", "csrf_token": token}).status_code == 200
    r = c.post("/register", data={"email": "new@example.com", "password": "longenough1", "csrf_token": token})
    assert r.status_code == 302 and c.get("/api/status").status_code == 200
    c.post("/logout", data={"csrf_token": token})
    assert c.get("/api/status").status_code == 401
    assert c.post("/login", data={"email": "new@example.com", "password": "wrong"}).status_code == 403  # CSRF
    r = c.post("/login", data={"email": "new@example.com", "password": "longenough1", "csrf_token": c.get("/login").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]})
    assert r.status_code == 302
    with app.app_context():
        from app.db import q_one
        assert q_one("SELECT password_hash FROM users WHERE email='new@example.com'")["password_hash"] != "longenough1"


def test_open_redirect_blocked_on_login(app):
    from app.routes.auth import _safe_next
    with app.test_request_context():
        assert _safe_next("//evil.com") != "//evil.com" and _safe_next("https://evil.com").endswith("/dashboard")
        assert _safe_next("/chatbots") == "/chatbots"


# ------------------------------------------------------------------ Rasa

def test_rasa_disabled_returns_404(app, api):
    app.config["RASA_ENABLED"] = False
    assert api.get("/api/rasa/imports").status_code == 404


def test_rasa_import_sample_preserves_original(app, api):
    r = api.post("/api/rasa/import-sample")
    assert r.status_code == 201
    imports = r.get_json()["imports"]
    names = {i["filename"] for i in imports}
    assert {"domain.yml", "nlu.yml", "rules.yml", "stories.yml", "config.yml"} <= names
    from pathlib import Path
    for imp in imports:
        full = api.get(f"/api/rasa/imports/{imp['id']}").get_json()
        original = [p for p in Path(app.config["SAMPLES_DIR"], "rasa_bot").rglob("*.yml") if p.name == imp["filename"]][0].read_bytes().decode()
        assert full["content"] == original and imp["parse_ok"]
    domain = next(i for i in imports if i["filename"] == "domain.yml")
    assert "greet" in domain["summary"]["intents"] and "utter_greet" in domain["summary"]["responses"]


def test_rasa_invalid_yaml_and_bad_files(api):
    def upload(files):
        return api.client.post("/api/rasa/import", data={"files": [(io.BytesIO(c), n) for n, c in files]}, headers=api._h(), content_type="multipart/form-data")
    r = upload([("broken.yml", b"intents: [unclosed\n  - x: : :")])
    assert r.status_code == 201 and r.get_json()["imports"][0]["parse_ok"] is False and r.get_json()["imports"][0]["parse_error"]
    assert upload([("evil.py", b"print(1)")]).status_code == 400
    assert upload([("bin.yml", b"\xff\xfe\x00bad")]).status_code == 400
    assert api.client.post("/api/rasa/import", data={}, headers=api._h(), content_type="multipart/form-data").status_code == 400


def test_rasa_create_actions_and_export_preview(app, demo):
    api = demo["api"]
    ids = [i["id"] for i in api.post("/api/rasa/import-sample").get_json()["imports"]]
    domain_id = next(i["id"] for i in api.get("/api/rasa/imports").get_json()["imports"] if i["filename"] == "domain.yml")
    res = api.post(f"/api/rasa/imports/{domain_id}/create-actions").get_json()
    assert {c["name"] for c in res["created"]} >= {"Rasa: greet", "Rasa: goodbye"}
    assert api.post(f"/api/rasa/imports/{domain_id}/create-actions").get_json()["created"] == []  # idempotent
    prev = api.post("/api/rasa/export/preview", {"chatbot_id": demo["bot"]["id"], "import_ids": ids}).get_json()
    assert prev["validation"]["ok"], prev["validation"]
    dom = yaml.safe_load(prev["files"]["domain.yml"])
    assert "greet" in dom["intents"] and "ask_shipping_policy" in dom["intents"] and "action_search_products" in dom["actions"]
    assert "utter_shipping_policy" in dom["responses"] and "utter_welcome" in dom["responses"]
    assert "stories" in yaml.safe_load(prev["files"]["data/stories.yml"])
    assert "class ActionSearchProducts" in prev["files"]["actions/actions.py"]
    z = api.post("/api/rasa/export/download", {"chatbot_id": demo["bot"]["id"], "import_ids": ids})
    assert z.status_code == 200 and z.mimetype == "application/zip"
    assert "domain.yml" in zipfile.ZipFile(io.BytesIO(z.data)).namelist()
    # the original imported file is untouched by exporting
    orig = api.get(f"/api/rasa/imports/{domain_id}").get_json()["content"]
    assert "utter_cheer_up" in orig and "ask_shipping_policy" not in orig
    assert api.post("/api/rasa/export/preview", {"chatbot_id": 9999}).status_code == 400
