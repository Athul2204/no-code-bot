import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.llm.base import LLMProvider, LLMResponse, ToolCall  # noqa: E402
from app.security import limiter  # noqa: E402
from app.seed import create_sample_database  # noqa: E402

CSRF = "test-csrf-token"


@pytest.fixture()
def app(tmp_path):
    inst = tmp_path / "instance"
    user_dbs = tmp_path / "user_dbs"
    app = create_app({
        "TESTING": True, "INSTANCE_DIR": str(inst), "DATABASE_PATH": str(inst / "app.db"),
        "USER_DB_DIR": str(user_dbs), "SECRET_KEY": "test-secret", "ENCRYPTION_KEY": "",
        "LLM_API_KEY": "", "LLM_PROVIDER": "anthropic", "CHAT_RATE_LIMIT": (1000, 60),
    })
    create_sample_database(user_dbs / "sample_store.db")
    limiter.reset()
    app.extensions.pop("llm_provider_override", None)
    return app


class Api:
    """Test client wrapper that signs in a user and sends the CSRF header."""

    def __init__(self, app, email="tester@example.com"):
        self.app = app
        self.client = app.test_client()
        with app.app_context():
            from app.db import execute, q_one
            from werkzeug.security import generate_password_hash
            row = q_one("SELECT id FROM users WHERE email = ?", (email,))
            self.user_id = row["id"] if row else execute("INSERT INTO users (email, password_hash) VALUES (?,?)", (email, generate_password_hash("password123")))
        with self.client.session_transaction() as s:
            s["user_id"] = self.user_id
            s["csrf_token"] = CSRF

    def _h(self):
        return {"X-CSRF-Token": CSRF}

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.client.post(url, json=json if json is not None else {}, headers=self._h(), **kw)

    def put(self, url, json=None):
        return self.client.put(url, json=json, headers=self._h())

    def delete(self, url):
        return self.client.delete(url, headers=self._h())


@pytest.fixture()
def api(app):
    return Api(app)


class FakeProvider(LLMProvider):
    """Scripted LLM: each call pops the next response (a callable gets the call args)."""
    name = "fake"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, system, messages, tools, **kw):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        item = self.script.pop(0) if self.script else LLMResponse("(no more scripted replies)")
        return item(self.calls[-1]) if callable(item) else item


def tool_call(name, args, cid="call_1"):
    return LLMResponse("", [ToolCall(cid, name, args)])


@pytest.fixture()
def use_llm(app):
    def _use(script):
        p = FakeProvider(script)
        app.extensions["llm_provider_override"] = p
        return p
    return _use


@pytest.fixture()
def demo(app, api):
    """Seed the demo data for the signed-in test user and return ids."""
    from app.seed import seed_demo, DEMO_EMAIL
    seed_demo(app)
    demo_api = Api(app, DEMO_EMAIL)
    bots = demo_api.get("/api/chatbots").get_json()["chatbots"]
    acts = {a["tool_name"]: a for a in demo_api.get("/api/actions").get_json()["actions"]}
    return {"api": demo_api, "bot": bots[0], "actions": acts}
