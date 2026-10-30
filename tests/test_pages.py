"""Frontend smoke tests: every page renders, assets exist, and nothing the CSP would block is used."""
import re
from pathlib import Path

import pytest

PAGES = ["/dashboard", "/chatbots", "/chatbots/new", "/connections", "/actions", "/playground", "/settings", "/rasa"]
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_login_required_for_pages(app):
    c = app.test_client()
    for url in PAGES:
        r = c.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


@pytest.mark.parametrize("url", PAGES)
def test_pages_render(api, url):
    r = api.get(url)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="csrf-token"' in html
    assert "<script>" not in html and "onclick=" not in html, "inline scripts/handlers are blocked by the CSP"
    for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
        assert api.get(ref).status_code == 200, ref


def test_edit_page_and_playground_carry_ids(api):
    bot = api.post("/api/chatbots", {"name": "Page Bot"}).get_json()
    assert f'data-bot-id="{bot["id"]}"' in api.get(f"/chatbots/{bot['id']}/edit").get_data(as_text=True)
    assert f'data-bot-id="{bot["id"]}"' in api.get(f"/playground?bot={bot['id']}").get_data(as_text=True)
    # a hostile query value is escaped, not injected
    html = api.get('/playground?bot="><script>alert(1)</script>').get_data(as_text=True)
    assert "<script>alert(1)" not in html


def test_compiled_css_and_scripts_exist():
    css = STATIC / "css" / "app.css"
    assert css.exists() and css.stat().st_size > 5000
    for name in ("app", "dashboard", "chatbots", "chatbot_form", "connections", "actions", "action_test", "playground", "settings", "rasa"):
        assert (STATIC / "js" / f"{name}.js").exists(), name


def test_unknown_page_renders_friendly_404(api):
    r = api.get("/definitely-not-here")
    assert r.status_code == 404 and "That page" in r.get_data(as_text=True)
