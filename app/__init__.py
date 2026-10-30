"""Application factory."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from flask import Flask

from . import db
from .config import load_config
from .security import apply_security_headers, csrf_token, current_user, verify_csrf


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(load_config(overrides))
    app.permanent_session_lifetime = timedelta(days=7)

    Path(app.config["USER_DB_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["INSTANCE_DIR"]).mkdir(parents=True, exist_ok=True)
    db.init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(db.close_db)

    app.before_request(verify_csrf)
    app.after_request(apply_security_headers)

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token(), "current_user": current_user(), "rasa_enabled": app.config["RASA_ENABLED"]}

    from .routes import api, auth, pages
    from .routes.helpers import register_error_handlers

    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    register_error_handlers(app)

    @app.cli.command("init-db")
    def _init_db_cmd():
        """Create tables (safe to re-run)."""
        db.init_db(app.config["DATABASE_PATH"])
        print("Database ready:", app.config["DATABASE_PATH"])

    @app.cli.command("seed-demo")
    def _seed_cmd():
        """Create the demo user, sample store database and demo chatbot."""
        from .seed import seed_demo

        print(seed_demo(app))

    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)
    return app
