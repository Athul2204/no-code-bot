"""Demo data: a sample store SQLite database, a demo user and a working read-only chatbot."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

from .db import dumps, execute, q_one
from .services import actions as action_svc
from .services import chatbots as bot_svc
from .services import connections as conn_svc

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-123"
SAMPLE_DB = "sample_store.db"

PRODUCTS = [
    (1, "Trail Running Shoes", "Footwear", 89.99, 24, "Lightweight shoes with grippy soles for muddy trails."),
    (2, "Waterproof Hiking Jacket", "Clothing", 129.00, 12, "Breathable 3-layer jacket, fully taped seams."),
    (3, "Insulated Water Bottle", "Accessories", 24.50, 120, "Keeps drinks cold for 24 hours or hot for 12."),
    (4, "Merino Wool Socks", "Clothing", 18.00, 80, "Cushioned, odour-resistant hiking socks."),
    (5, "Ultralight Tent (2 person)", "Camping", 249.00, 6, "Three-season tent weighing only 1.4 kg."),
    (6, "Down Sleeping Bag", "Camping", 199.99, 9, "Rated to -5 C, packs down to the size of a loaf."),
    (7, "Headlamp 400 lumens", "Accessories", 34.95, 45, "USB-rechargeable with red night mode."),
    (8, "Trekking Poles (pair)", "Accessories", 59.00, 30, "Collapsible carbon poles with cork grips."),
    (9, "Camp Stove", "Camping", 44.00, 18, "Compact canister stove, boils 1 L in 3.5 minutes."),
    (10, "Road Running Shoes", "Footwear", 109.00, 0, "Cushioned daily trainers (currently out of stock)."),
    (11, "Fleece Midlayer", "Clothing", 64.00, 22, "Warm, quick-drying 200-weight fleece."),
    (12, "First Aid Kit", "Accessories", 29.99, 60, "Compact kit for day hikes and short trips."),
]
ORDERS = [
    (1001, "Asha Patel", 1, 1, "shipped", "2026-10-02"),
    (1002, "Liam Chen", 5, 1, "processing", "2026-10-20"),
    (1003, "Maria Garcia", 3, 2, "delivered", "2026-09-28"),
    (1004, "Noah Smith", 2, 1, "cancelled", "2026-10-11"),
    (1005, "Priya Singh", 6, 1, "shipped", "2026-10-22"),
    (1006, "Omar Ali", 8, 2, "processing", "2026-10-25"),
]


def create_sample_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                                   price REAL NOT NULL, stock INTEGER NOT NULL, description TEXT);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
                                 quantity INTEGER NOT NULL, status TEXT NOT NULL, ordered_at TEXT NOT NULL);
            CREATE TABLE support_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, subject TEXT NOT NULL,
                                          message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", PRODUCTS)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", ORDERS)
        conn.commit()
    finally:
        conn.close()


def seed_demo(app) -> str:
    with app.app_context():
        create_sample_database(Path(app.config["USER_DB_DIR"]) / SAMPLE_DB)
        user = q_one("SELECT * FROM users WHERE email = ?", (DEMO_EMAIL,))
        created_user = user is None
        if created_user:
            uid = execute("INSERT INTO users (email, password_hash) VALUES (?,?)", (DEMO_EMAIL, generate_password_hash(DEMO_PASSWORD)))
        else:
            uid = user["id"]
        if q_one("SELECT 1 FROM chatbots WHERE user_id = ? AND name = ?", (uid, "Outdoor Store Assistant")):
            return f"Demo already present. Sign in with {DEMO_EMAIL}."
        conn = q_one("SELECT id FROM database_connections WHERE user_id = ? AND name = ?", (uid, "Sample Store DB"))
        conn_id = conn["id"] if conn else conn_svc.create_connection(uid, {"name": "Sample Store DB", "db_type": "sqlite", "config": {"file": SAMPLE_DB}})["id"]

        search = action_svc.create_action(uid, {
            "name": "Search products", "template_type": "db_search", "connection_id": conn_id,
            "description": "Search the product catalogue by name, category or maximum price.",
            "when_to_use": "The customer asks what we sell, wants product suggestions, prices or stock.",
            "config": {"table": "products", "output_fields": ["id", "name", "category", "price", "stock", "description"], "limit": 8},
            "parameters": [
                {"name": "name", "label": "Product name", "description": "Part of the product name", "maps_to": "name", "extra": {"operator": "contains"}},
                {"name": "category", "label": "Category", "description": "Footwear, Clothing, Accessories or Camping", "maps_to": "category",
                 "extra": {"operator": "eq", "choices": ["Footwear", "Clothing", "Accessories", "Camping"]}},
                {"name": "max_price", "label": "Maximum price", "param_type": "number", "description": "Highest price the customer will pay", "maps_to": "price", "extra": {"operator": "lte"}},
            ]})
        order = action_svc.create_action(uid, {
            "name": "Check order status", "template_type": "db_get_by_id", "connection_id": conn_id,
            "description": "Look up an order by its order number and report its status.",
            "when_to_use": "The customer asks where their order is or what its status is.",
            "config": {"table": "orders", "id_column": "id", "output_fields": ["id", "customer_name", "quantity", "status", "ordered_at"],
                       "id_description": "The order number, for example 1001"}})
        policy = action_svc.create_action(uid, {
            "name": "Shipping policy", "template_type": "static_response",
            "description": "Explain shipping times and the return policy.",
            "when_to_use": "The customer asks about delivery, shipping costs or returns.",
            "config": {"response_text": "Standard shipping takes 3-5 business days and is free over $75. Returns are accepted within 30 days of delivery in unused condition."},
            "parameters": []})
        bot = bot_svc.create_bot(uid, {
            "name": "Outdoor Store Assistant", "description": "Answers product and order questions for a small outdoor gear shop.",
            "purpose": "Help customers find outdoor gear, check order status and understand shipping and returns.",
            "welcome_message": "Hi! I'm the Outdoor Store assistant. Ask me about products, an order number, or shipping.",
            "system_instructions": "Be friendly and brief. Always quote prices in dollars. If a product is out of stock, say so and suggest an alternative.",
            "enabled_actions": [search["id"], order["id"], policy["id"]], "test_mode_default": True})
        return (f"Demo ready. Sign in with {DEMO_EMAIL} / {DEMO_PASSWORD}" if created_user else f"Demo chatbot added to {DEMO_EMAIL}") + f" (chatbot #{bot['id']})."
