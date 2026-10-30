# Bot Builder: a no-code AI chatbot platform

Create AI chatbots, connect a database, and give the bot **actions** (search records, look up an order, call a web API, send a fixed reply…) using only buttons and forms. No Python, SQL or YAML needed. An LLM with tool calling decides *when* to use an action; the server decides *whether it is allowed* and runs it.

**Stack:** Flask · SQLite · HTML · Tailwind CSS · vanilla JavaScript · pluggable LLM providers (Anthropic, or any OpenAI-compatible API) · optional Rasa import/export.

## Quick start (Windows PowerShell)

Requires Python 3.10+ (`py --version`). Run these from the project folder:

```powershell
# 1. Create and activate a virtual environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1
#   If PowerShell blocks the script, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your settings file
Copy-Item .env.example .env

# 4. Create the database + demo data (demo user, sample store database, working demo chatbot)
python scripts\init_db.py

# 5. Start the app
python wsgi.py
```

Open <http://127.0.0.1:5000> and sign in with **demo@example.com / demo-password-123** (or create your own account). Open **My Chatbots → Outdoor Store Assistant → Open playground**.

Other commands:

```powershell
python scripts\init_db.py --no-demo     # tables only, no demo data
python -m pytest -q                     # run the test suite
flask --app wsgi init-db                # same as init_db.py --no-demo
flask --app wsgi seed-demo              # add the demo data later
```

macOS/Linux: use `python3 -m venv .venv`, `source .venv/bin/activate`, `cp .env.example .env`.

## Turning on the AI

The app works without an API key: you can build chatbots, connect databases and **test every action directly** (Custom Actions → Test, or the playground's "Try actions directly" panel). To chat naturally you need an LLM:

1. Open **Settings**, choose a provider, paste your API key, click **Test connection**. The key is stored **encrypted** and is never shown again; *or*
2. Put `LLM_PROVIDER`, `LLM_API_KEY` (and optionally `LLM_MODEL`, `LLM_BASE_URL`) in `.env` and restart.

`openai` mode works with OpenAI, OpenRouter, Ollama and any OpenAI-compatible endpoint (for a local Ollama server also set `ALLOW_PRIVATE_LLM_URLS=true`). With no key, the chat shows a clear setup message instead of failing.

## How a non-technical user uses it

1. **Connect Database**: pick the sample store (or upload a `.db`/`.sqlite` file). Test it, browse tables and columns with dropdowns, preview rows. Connections are **read-only** until you allow writes.
2. **Custom Actions → New action**: choose a template, then fill a form with dropdowns for tables and columns:
   *Search records · Retrieve by ID · Insert · Update · Call a REST API · Predefined response · Custom (form)*.
   Actions that change data start **unapproved**; click *Review & approve* to allow them.
3. **Create Chatbot**: describe its purpose in plain English, set a welcome message and instructions, tick the actions it may use.
4. **Chat Playground**: chat, watch action cards, confirm sensitive actions. **Test mode** (on by default) only *simulates* writes.
5. **Rasa Import / Export** (optional): import existing `.yml` files, preview and download a generated project.

## Safety model

The LLM only ever *requests* an action by name with arguments. Everything else is enforced server-side (`app/services/chat.py`, `app/actions/executor.py`):

| Risk | Control |
| --- | --- |
| LLM runs SQL / code / shell | It can't. Actions are validated JSON configs run by fixed code. SQL is parameterized; table and column names are whitelisted from the live schema. |
| LLM enables or invents actions | Only actions the chatbot enables **and** the owner approved (and that are switched on) are callable. Unknown names are rejected and logged. |
| Unwanted writes | Writes need: write-enabled connection, owner-approved action, and a per-use **user confirmation** (separate request, single-use token). Test mode never writes. |
| Bad arguments | Validated and coerced against each action's declared parameters; missing values make the bot ask the user. |
| Credentials leaking | DB settings, API keys and REST secrets are Fernet-encrypted at rest and never sent to the browser or the LLM. |
| SSRF / unsafe URLs | `http(s)` only; hosts resolved and blocked if private/loopback/link-local (DNS pinned); redirects are not followed; timeouts and size caps; optional `ALLOWED_API_HOSTS`. |
| Web attacks | CSRF tokens on all state-changing requests, strict CSP (no inline script, no CDN), output escaping (`textContent` / Jinja autoescape), security headers, hashed passwords, login and chat rate limits, open-redirect protection. |
| Auditing | Every execution (including refusals) is stored in `action_execution_logs` and shown on the Custom Actions page. |

**Python actions** (advanced, **off by default**; `ENABLE_PYTHON_ACTIONS=true`): code is statically checked, requires approval (re-required after every edit) and runs in a separate restricted subprocess, never inside Flask. It is defense in depth, not a perfect sandbox. On Windows the CPU/memory limits are not applied, so for production run the app in a container without network access.

## Project layout

```
app/
  __init__.py            application factory
  config.py              .env -> Flask config, persisted secrets
  schema.sql, db.py      SQLite schema (10 required tables + support tables), helpers
  security.py            encryption, CSRF, auth, rate limiting, SSRF-safe HTTP
  validation.py          input validation and runtime argument coercion
  routes/                pages (HTML), api (JSON), auth, error handlers
  services/              chatbots, actions, connections, chat orchestration
  actions/               templates (validated configs), executor, Python sandbox
  integrations/databases/ adapter interface + SQLite (add MySQL/PostgreSQL here)
  llm/                   provider interface, Anthropic, OpenAI-compatible
  rasa/                  importer + exporter (optional, RASA_ENABLED)
  templates/, static/    Jinja pages, compiled Tailwind CSS, per-page JavaScript
samples/rasa_bot/        sample Rasa project used by the importer demo
scripts/init_db.py       database initialisation
tests/                   pytest suite
```

**Adding MySQL/PostgreSQL:** implement `DatabaseAdapter` (`app/integrations/databases/base.py`) and register it in `ENGINES`. The UI already lists them as "coming soon".

## Editing the styles (optional)

The compiled stylesheet is committed (`app/static/css/app.css`), so Node is **not** needed to run the app. To change styles: `npm install`, then `npm run build:css` (or `npm run watch:css`).

## Notes and limits

* Replies are returned whole (no token streaming); the playground shows a typing indicator.
* The rate limiter is in-memory and per process; use a shared store if you run several workers.
* Flask's built-in server (`python wsgi.py`) is for development. For production, use a WSGI server such as `waitress` (works on Windows): `pip install waitress; waitress-serve --host=127.0.0.1 --port=5000 wsgi:app`, behind HTTPS with `SESSION_COOKIE_SECURE=true`.
* Back up `instance/` (database and encryption key). Without the key, stored secrets can't be decrypted.
