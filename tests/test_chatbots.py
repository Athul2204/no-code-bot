def valid(**over):
    base = {"name": "Helper Bot", "description": "d", "purpose": "Help people", "welcome_message": "Hi", "system_instructions": "Be nice"}
    base.update(over)
    return base


def test_create_edit_duplicate_delete(api):
    r = api.post("/api/chatbots", valid())
    assert r.status_code == 201
    bot = r.get_json()
    assert bot["name"] == "Helper Bot" and bot["version"] == 1 and bot["enabled_actions"] == []

    r = api.put(f"/api/chatbots/{bot['id']}", valid(name="Renamed", temperature=0.5))
    assert r.status_code == 200 and r.get_json()["version"] == 2 and r.get_json()["temperature"] == 0.5

    dup = api.post(f"/api/chatbots/{bot['id']}/duplicate").get_json()
    assert dup["name"] == "Renamed (copy)" and dup["id"] != bot["id"]
    assert len(api.get("/api/chatbots").get_json()["chatbots"]) == 2

    assert api.delete(f"/api/chatbots/{bot['id']}").status_code == 200
    assert api.get(f"/api/chatbots/{bot['id']}").status_code == 404


def test_validation_errors(api):
    r = api.post("/api/chatbots", valid(name=""))
    assert r.status_code == 400 and "name" in r.get_json()["fields"]
    r = api.post("/api/chatbots", valid(name="x" * 200))
    assert r.status_code == 400
    r = api.post("/api/chatbots", valid(temperature=5))
    assert "temperature" in r.get_json()["fields"]
    r = api.post("/api/chatbots", valid(enabled_actions=[999]))
    assert r.status_code == 400 and "enabled_actions" in r.get_json()["fields"]


def test_versions_and_restore(api):
    bot = api.post("/api/chatbots", valid()).get_json()
    api.put(f"/api/chatbots/{bot['id']}", valid(name="Second"))
    versions = api.get(f"/api/chatbots/{bot['id']}/versions").get_json()["versions"]
    assert [v["version_number"] for v in versions] == [2, 1]
    restored = api.post(f"/api/chatbots/{bot['id']}/versions/{versions[1]['id']}/restore").get_json()
    assert restored["name"] == "Helper Bot" and restored["version"] == 3


def test_cascade_delete_removes_conversations(app, api):
    bot = api.post("/api/chatbots", valid()).get_json()
    with app.app_context():
        from app.db import execute, q_one
        cid = execute("INSERT INTO conversations (chatbot_id, user_id) VALUES (?,?)", (bot["id"], api.user_id))
        execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'hi')", (cid,))
    api.delete(f"/api/chatbots/{bot['id']}")
    with app.app_context():
        assert q_one("SELECT count(*) n FROM messages")["n"] == 0
        assert q_one("SELECT count(*) n FROM conversations")["n"] == 0


def test_users_are_isolated(app, api):
    bot = api.post("/api/chatbots", valid()).get_json()
    from tests.conftest import Api
    other = Api(app, "other@example.com")
    assert other.get(f"/api/chatbots/{bot['id']}").status_code == 404
    assert other.delete(f"/api/chatbots/{bot['id']}").status_code == 404
    assert other.get("/api/chatbots").get_json()["chatbots"] == []


def test_requires_login_and_csrf(app):
    c = app.test_client()
    assert c.get("/api/chatbots").status_code == 401
    from tests.conftest import Api
    a = Api(app)
    r = a.client.post("/api/chatbots", json=valid())  # no CSRF header
    assert r.status_code == 403
