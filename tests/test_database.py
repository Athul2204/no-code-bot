import sqlite3
from pathlib import Path

import pytest

from app.integrations.databases import DatabaseError
from app.integrations.databases.sqlite_adapter import SQLiteAdapter, resolve_db_file
from tests.test_actions import make_conn, search_payload


def adapter(app, writes=False):
    return SQLiteAdapter(Path(app.config["USER_DB_DIR"]) / "sample_store.db", writes)


def test_connection_lifecycle_and_schema(api):
    conn = make_conn(api)
    assert conn["config"] == {"file": "sample_store.db"} and conn["allow_writes"] is False
    assert api.post(f"/api/connections/{conn['id']}/test").get_json()["ok"]
    schema = api.get(f"/api/connections/{conn['id']}/schema").get_json()["tables"]
    assert {t["name"] for t in schema} == {"products", "orders", "support_tickets"}
    products = next(t for t in schema if t["name"] == "products")
    assert {c["name"] for c in products["columns"]} >= {"id", "name", "price"}
    prev = api.post(f"/api/connections/{conn['id']}/preview", {"table": "products", "columns": ["name", "price"], "limit": 3}).get_json()
    assert len(prev["rows"]) == 3 and set(prev["rows"][0]) == {"name", "price"}
    assert api.post(f"/api/connections/{conn['id']}/preview", {"table": "nope"}).status_code == 400
    assert api.delete(f"/api/connections/{conn['id']}").status_code == 200


def test_credentials_encrypted_and_hidden(app, api):
    conn = make_conn(api)
    with app.app_context():
        from app.db import q_one
        raw = q_one("SELECT config_encrypted FROM database_connections WHERE id = ?", (conn["id"],))["config_encrypted"]
    assert "sample_store" not in raw and raw.startswith("gAAAA")
    assert "config_encrypted" not in api.get("/api/connections").get_data(as_text=True)


def test_connection_validation(api):
    assert api.post("/api/connections", {"name": "x", "db_type": "mysql", "config": {}}).status_code == 400
    r = api.post("/api/connections", {"name": "Bad", "db_type": "sqlite", "config": {"file": "../../etc/passwd"}})
    assert r.status_code == 400
    r = api.post("/api/connections", {"name": "Missing", "db_type": "sqlite", "config": {"file": "nothere.db"}})
    assert r.status_code == 400
    make_conn(api)
    assert api.post("/api/connections", {"name": "Store", "config": {"file": "sample_store.db"}}).status_code == 400  # duplicate name


def test_path_traversal_blocked(app):
    for bad in ["../app.db", "/etc/passwd", "a/b.db", "..\\x.db", "x.txt", ""]:
        with pytest.raises(DatabaseError):
            resolve_db_file(app.config["USER_DB_DIR"], bad)


def test_upload_database(app, api, tmp_path):
    f = tmp_path / "mine.db"
    c = sqlite3.connect(f); c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"); c.commit(); c.close()
    with open(f, "rb") as fh:
        r = api.client.post("/api/connections/upload", data={"file": (fh, "mine.db")}, headers=api._h(), content_type="multipart/form-data")
    assert r.status_code == 201 and "mine.db" in r.get_json()["files"]
    fake = tmp_path / "fake.db"; fake.write_text("not a database")
    with open(fake, "rb") as fh:
        r = api.client.post("/api/connections/upload", data={"file": (fh, "fake.db")}, headers=api._h(), content_type="multipart/form-data")
    assert r.status_code == 400


def test_search_lookup_and_injection_safe(app):
    a = adapter(app)
    rows = a.select("products", ["name", "price"], [{"column": "name", "op": "contains", "value": "shoes"}], 10)
    assert {r["name"] for r in rows} == {"Trail Running Shoes", "Road Running Shoes"}
    rows = a.select("products", None, [{"column": "price", "op": "lte", "value": 30}], 50)
    assert all(r["price"] <= 30 for r in rows) and rows
    # injection attempts are just values
    assert a.select("products", ["name"], [{"column": "name", "op": "eq", "value": "x' OR '1'='1"}], 10) == []
    assert a.select("products", ["name"], [{"column": "name", "op": "contains", "value": "%"}], 10) == []  # % is literal
    assert len(a.list_tables()) == 3
    with pytest.raises(DatabaseError):
        a.select("products", ["name"], [{"column": 'name" OR 1=1 --', "op": "eq", "value": 1}], 5)
    with pytest.raises(DatabaseError):
        a.select("products; DROP TABLE products", None, [], 5)
    assert a.get_by_id("orders", "id", 1001, ["status"]) == {"status": "shipped"}
    assert a.get_by_id("orders", "id", 424242, None) is None
    assert len(a.select("products", None, [], 9999)) <= 50  # limit capped


def test_read_only_by_default(app):
    a = adapter(app, writes=False)
    with pytest.raises(DatabaseError, match="read-only"):
        a.insert("support_tickets", {"email": "a@b.c", "subject": "s"})
    with pytest.raises(DatabaseError, match="read-only"):
        a.update("products", "id", 1, {"price": 1})
    assert adapter(app).get_by_id("products", "id", 1, ["price"])["price"] == 89.99  # untouched


def test_write_when_enabled_and_dry_run(app):
    a = adapter(app, writes=True)
    sim = a.insert("support_tickets", {"email": "a@b.c", "subject": "s"}, dry_run=True)
    assert sim["simulated"]
    with pytest.raises(DatabaseError, match="required"):
        a.insert("support_tickets", {"email": "a@b.c"}, dry_run=True)
    res = a.insert("support_tickets", {"email": "a@b.c", "subject": "s"})
    assert res["inserted_id"] == 1
    assert a.update("products", "id", 1, {"price": 95.5}, dry_run=True)["simulated"]
    assert a.get_by_id("products", "id", 1, ["price"])["price"] == 89.99
    assert a.update("products", "id", 1, {"price": 95.5})["rows_affected"] == 1
    assert a.get_by_id("products", "id", 1, ["price"])["price"] == 95.5


def test_run_search_action_end_to_end(api):
    conn = make_conn(api)
    a = api.post("/api/actions", search_payload(conn["id"])).get_json()
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"name": "jacket"}}).get_json()
    assert r["ok"] and r["data"] == [{"name": "Waterproof Hiking Jacket", "price": 129.0}]
    r = api.post(f"/api/actions/{a['id']}/test", {"arguments": {"max_price": "abc"}}).get_json()
    assert not r["ok"] and r["status"] == "invalid"
    logs = api.get("/api/logs").get_json()["logs"]
    assert logs[0]["action_name"] == "find_products" and logs[0]["source"] == "manual_test"


def test_get_by_id_action(api):
    conn = make_conn(api)
    a = api.post("/api/actions", {"name": "Order lookup", "description": "d", "template_type": "db_get_by_id", "connection_id": conn["id"],
                                  "config": {"table": "orders", "id_column": "id", "output_fields": ["status"]}}).get_json()
    assert a["parameters"][0]["name"] == "id" and a["parameters"][0]["param_type"] == "integer"
    assert api.post(f"/api/actions/{a['id']}/test", {"arguments": {"id": "1002"}}).get_json()["data"] == {"status": "processing"}
    assert api.post(f"/api/actions/{a['id']}/test", {"arguments": {"id": 5}}).get_json()["data"] is None


def test_write_action_flow_test_mode_and_real(api, app):
    conn = make_conn(api)
    a = api.post("/api/actions", {"name": "Add ticket", "description": "d", "template_type": "db_insert", "connection_id": conn["id"],
                                  "config": {"table": "support_tickets"},
                                  "parameters": [{"name": "email", "maps_to": "email", "required": True}, {"name": "subject", "maps_to": "subject", "required": True}]}).get_json()
    args = {"arguments": {"email": "x@y.z", "subject": "Help"}}
    # dry-run allowed before approval, changes nothing
    r = api.post(f"/api/actions/{a['id']}/test", {**args, "test_mode": True}).get_json()
    assert r["ok"] and r["simulated"]
    # real run blocked: not approved
    r = api.post(f"/api/actions/{a['id']}/test", {**args, "test_mode": False}).get_json()
    assert not r["ok"] and r["status"] == "rejected"
    api.post(f"/api/actions/{a['id']}/approve", {"approved": True})
    # approved but connection still read-only
    r = api.post(f"/api/actions/{a['id']}/test", {**args, "test_mode": False}).get_json()
    assert not r["ok"] and "read-only" in r["error"]
    api.post(f"/api/connections/{conn['id']}/writes", {"allow": True})
    r = api.post(f"/api/actions/{a['id']}/test", {**args, "test_mode": False}).get_json()
    assert r["ok"] and not r["simulated"] and r["data"]["inserted_id"] == 1
    c = sqlite3.connect(Path(app.config["USER_DB_DIR"]) / "sample_store.db")
    assert c.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1
    c.close()
