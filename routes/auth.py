from __future__ import annotations

import re

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import execute, q_one
from ..security import current_user, limiter

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("pages.dashboard")


def _login(user_id: int) -> None:
    csrf = session.get("csrf_token")
    session.clear()  # start a clean session on login
    if csrf:  # keep the CSRF token so forms already open in another tab keep working
        session["csrf_token"] = csrf
    session["user_id"] = user_id
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        ok, _ = limiter.allow(f"login:{request.remote_addr}", 10, 60)
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = q_one("SELECT * FROM users WHERE email = ?", (email,))
        if not ok:
            error = "Too many attempts. Please wait a minute."
        elif user and check_password_hash(user["password_hash"], password):
            _login(user["id"])
            return redirect(_safe_next(request.args.get("next")))
        else:
            error = "Incorrect email or password."
    return render_template("login.html", error=error, mode="login", allow_registration=current_app.config["ALLOW_REGISTRATION"],
                           user=current_user())


@bp.route("/register", methods=["GET", "POST"])
def register():
    if not current_app.config["ALLOW_REGISTRATION"]:
        return redirect(url_for("auth.login"))
    error = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not EMAIL_RE.match(email):
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif len(password) > 200:
            error = "Password is too long."
        elif q_one("SELECT 1 FROM users WHERE email = ?", (email,)):
            error = "An account with that email already exists."
        else:
            uid = execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, generate_password_hash(password)))
            _login(uid)
            return redirect(url_for("pages.dashboard"))
    return render_template("login.html", error=error, mode="register", allow_registration=True, user=current_user())


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
