from app.validation import coerce_args


def make_conn(api, **over):
    body = {"name": "Store", "db_type": "sqlite", "config": {"file": "sample_store.db"}}
    body.update(over)
    r = api.post("/api/connections", body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def search_payload(conn_id, **over):
    p = {"name": "Find products", "description": "Find products", "template_type": "db_search", "connection_id": conn_id,
         "config": {"table": "products", "output_fields": ["name", "price"], "limit": 5},
         "parameters": [{"name": "name", "label": "Name", "maps_to": "name", "extra": {"operator": "contains"}},
                        {"name": "max_price", "label": "Max price", "param_type": "number", "maps_to": "price", "extra": {"operator": "lte"}}]}
    p.update(over)
    return p


def test_action_templates_listed(api):
    types = {t["type"] for t in api.get("/api/action-templates").get_json()["templates"]}
    assert types == {"db_search", "db_get_by_id", "db_insert", "db_update", "rest_api", "static_response", "custom_form"}


def test_create_search_action_and_slug(api):
    conn = make_conn(api)
    r = api.post("/api/actions", search_payload(conn["id"]))
    assert r.status_code == 201
    a = r.get_json()
    assert a["tool_name"] == "find_products" and not a["needs_approval"] and a["enabled"]
    assert [p["name"] for p in a["parameters"]] == ["name", "max_price"]
    # duplicate names get unique tool names
    a2 = api.post("/api/actions", search_payload(conn["id"])).get_json()
    assert a2["tool_name"] == "find_products_2"


def test_action_config_validation(api):
    conn = make_conn(api)
    r = api.post("/api/actions", search_payload(conn["id"], config={"table": "nope"}))
    assert r.status_code == 400 and "table" in r.get_json()["fields"]
    bad_col = search_payload(conn["id"])
    bad_col["parameters"][0]["maps_to"] = "secret; DROP TABLE products"
    assert "parameters.0.maps_to" in api.post("/api/actions", bad_col).get_json()["fields"]
    bad_op = search_payload(conn["id"])
    bad_op["parameters"][0]["extra"]["operator"] = "like; --"
    assert "parameters.0.operator" in api.post("/api/actions", bad_op).get_json()["fields"]
    assert "connection_id" in api.post("/api/actions", search_payload(9999)).get_json()["fields"]
    assert "tool_name" in api.post("/api/actions", search_payload(conn["id"], tool_name="Bad Name!")).get_json()["fields"]
    assert "parameters" in api.post("/api/actions", search_payload(conn["id"], parameters=[])).get_json()["fields"]
    dup = search_payload(conn["id"])
    dup["parameters"][1]["name"] = "name"
    assert "parameters.1.name" in api.post("/api/actions", dup).get_json()["fields"]


def test_insert_requires_not_null_columns(api):
    conn = make_conn(api)
    payload = {"name": "New ticket", "description": "Create a ticket", "template_type": "db_insert", "connection_id": conn["id"],
               "config": {"table": "support_tickets"}, "parameters": [{"name": "email", "maps_to": "email"}]}
    r = api.post("/api/actions", payload)
    assert r.status_code == 400 and "subject" in r.get_json()["fields"]["parameters"]


def test_parameter_validation_unit():
    params = [
        {"name": "q", "label": "Query", "param_type": "string", "required": True, "extra": {}},
        {"name": "n", "label": "Count", "param_type": "integer", "required": False, "extra": {}},
        {"name": "p", "label": "Price", "param_type": "number", "required": False, "extra": {}},
        {"name": "ok", "label": "Flag", "param_type": "boolean", "required": False, "extra": {}},
        {"name": "c", "label": "Colour", "param_type": "string", "required": False, "extra": {"choices": ["Red", "Blue"]}},
    ]
    clean, missing, errors = coerce_args(params, {"q": "  shoes ", "n": "3", "p": "9.5", "ok": "yes", "c": "red", "extra": "dropped"})
    assert clean == {"q": "shoes", "n": 3, "p": 9.5, "ok": True, "c": "Red"} and not missing and not errors
    _, missing, _ = coerce_args(params, {})
    assert missing == ["q"]
    _, _, errors = coerce_args(params, {"q": "x", "n": "abc", "p": "nan", "c": "green", "ok": "maybe"})
    assert set(errors) == {"n", "p", "c", "ok"}
    _, _, errors = coerce_args(params, {"q": "x" * 5000})
    assert "q" in errors
    _, _, errors = coerce_args(params, {"q": {"nested": 1}})
    assert "q" in errors


def test_write_actions_need_approval_and_edit_revokes(api):
    conn = make_conn(api)
    payload = {"name": "Add ticket", "description": "d", "template_type": "db_insert", "connection_id": conn["id"],
               "config": {"table": "support_tickets"},
               "parameters": [{"name": "email", "maps_to": "email", "required": True}, {"name": "subject", "maps_to": "subject", "required": True},
                              {"name": "message", "maps_to": "message"}]}
    a = api.post("/api/actions", payload).get_json()
    assert a["is_write"] and a["needs_approval"] and not a["approved"] and a["requires_confirmation"]
    a = api.post(f"/api/actions/{a['id']}/approve", {"approved": True}).get_json()
    assert a["approved"]
    a = api.put(f"/api/actions/{a['id']}", payload).get_json()
    assert not a["approved"]  # editing revokes approval


def test_read_action_cannot_be_approved(api):
    conn = make_conn(api)
    a = api.post("/api/actions", search_payload(conn["id"])).get_json()
    assert api.post(f"/api/actions/{a['id']}/approve", {"approved": True}).status_code == 400


def test_delete_action_removes_from_chatbot(api):
    conn = make_conn(api)
    a = api.post("/api/actions", search_payload(conn["id"])).get_json()
    bot = api.post("/api/chatbots", {"name": "Bot", "enabled_actions": [a["id"]]}).get_json()
    assert bot["enabled_actions"] == [a["id"]]
    api.delete(f"/api/actions/{a['id']}")
    assert api.get(f"/api/chatbots/{bot['id']}").get_json()["enabled_actions"] == []


def test_static_and_custom_templates(api):
    s = api.post("/api/actions", {"name": "Hours", "description": "Opening hours", "template_type": "static_response",
                                  "config": {"response_text": "Open {day} 9-5"}, "parameters": [{"name": "day", "required": True}]}).get_json()
    r = api.post(f"/api/actions/{s['id']}/test", {"arguments": {"day": "Monday"}}).get_json()
    assert r["ok"] and r["data"]["response"] == "Open Monday 9-5"
    r = api.post(f"/api/actions/{s['id']}/test", {"arguments": {}}).get_json()
    assert not r["ok"] and r["missing"] == ["day"]
    bad = api.post("/api/actions", {"name": "Hours2", "description": "x", "template_type": "static_response",
                                    "config": {"response_text": "Hello {unknown}"}, "parameters": []})
    assert "response_text" in bad.get_json()["fields"]
    c = api.post("/api/actions", {"name": "Greeter", "description": "Greets", "template_type": "custom_form",
                                  "config": {"result_template": "Hello {who}!"}, "parameters": [{"name": "who", "required": True}]})
    assert c.status_code == 201
    assert api.post(f"/api/actions/{c.get_json()['id']}/test", {"arguments": {"who": "Sam"}}).get_json()["data"]["response"] == "Hello Sam!"


def test_secrets_never_returned(api):
    a = api.post("/api/actions", {"name": "Weather", "description": "w", "template_type": "rest_api",
                                  "config": {"url": "https://api.example.com/v1/{city}", "method": "GET", "auth_header": "X-Api-Key", "output_fields": ["temp"]},
                                  "parameters": [{"name": "city", "extra": {"location": "path"}}], "secrets": {"auth_value": "super-secret-123"}})
    assert a.status_code == 201
    assert "super-secret-123" not in a.get_data(as_text=True)
    listing = api.get("/api/actions").get_data(as_text=True)
    assert "super-secret-123" not in listing and '"has_secret":true' in listing.replace(" ", "")
