from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for

from ..security import login_required
from ..services import chatbots as bot_svc

bp = Blueprint("pages", __name__)


@bp.get("/")
@login_required
def index():
    return redirect(url_for("pages.dashboard"))


@bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", page="dashboard", title="Dashboard")


@bp.get("/chatbots")
@login_required
def chatbots():
    return render_template("chatbots.html", page="chatbots", title="My Chatbots")


@bp.get("/chatbots/new")
@login_required
def chatbot_new():
    return render_template("chatbot_form.html", page="chatbot_new", title="Create Chatbot", bot_id=0)


@bp.get("/chatbots/<int:bot_id>/edit")
@login_required
def chatbot_edit(bot_id: int):
    return render_template("chatbot_form.html", page="chatbot_new", title="Edit Chatbot", bot_id=bot_id)


@bp.get("/connections")
@login_required
def connections():
    return render_template("connections.html", page="connections", title="Connect Database")


@bp.get("/actions")
@login_required
def actions():
    return render_template("actions.html", page="actions", title="Custom Actions")


@bp.get("/playground")
@login_required
def playground():
    return render_template("playground.html", page="playground", title="Chat Playground")


@bp.get("/settings")
@login_required
def settings():
    return render_template("settings.html", page="settings", title="Settings")


@bp.get("/rasa")
@login_required
def rasa():
    return render_template("rasa.html", page="rasa", title="Rasa Import")
