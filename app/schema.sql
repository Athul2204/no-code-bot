PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    is_secret  INTEGER NOT NULL DEFAULT 0,   -- 1 => value is Fernet-encrypted
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS chatbots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    purpose             TEXT NOT NULL DEFAULT '',
    welcome_message     TEXT NOT NULL DEFAULT '',
    system_instructions TEXT NOT NULL DEFAULT '',
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_chatbots_user ON chatbots(user_id);

CREATE TABLE IF NOT EXISTS chatbot_settings (
    chatbot_id        INTEGER PRIMARY KEY REFERENCES chatbots(id) ON DELETE CASCADE,
    enabled_actions   TEXT NOT NULL DEFAULT '[]',   -- JSON list of custom_actions.id
    temperature       REAL NOT NULL DEFAULT 0.2,
    max_tool_rounds   INTEGER NOT NULL DEFAULT 5,
    model_override    TEXT NOT NULL DEFAULT '',
    test_mode_default INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS database_connections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    db_type          TEXT NOT NULL DEFAULT 'sqlite',
    config_encrypted TEXT NOT NULL,                 -- Fernet(JSON); never sent to the browser or the LLM
    allow_writes     INTEGER NOT NULL DEFAULT 0,    -- read-only unless explicitly approved
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_connections_user ON database_connections(user_id);

CREATE TABLE IF NOT EXISTS custom_actions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                  TEXT NOT NULL,            -- human label
    tool_name             TEXT NOT NULL,            -- identifier exposed to the LLM
    description           TEXT NOT NULL DEFAULT '',
    when_to_use           TEXT NOT NULL DEFAULT '',
    template_type         TEXT NOT NULL,
    connection_id         INTEGER REFERENCES database_connections(id) ON DELETE SET NULL,
    config                TEXT NOT NULL DEFAULT '{}',   -- structured JSON, validated against the template
    secrets_encrypted     TEXT NOT NULL DEFAULT '',     -- Fernet(JSON) e.g. API auth header value
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    is_write              INTEGER NOT NULL DEFAULT 0,
    approved              INTEGER NOT NULL DEFAULT 0,   -- explicit approval for write / code actions
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (user_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_actions_user ON custom_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_actions_connection ON custom_actions(connection_id);

CREATE TABLE IF NOT EXISTS action_parameters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id   INTEGER NOT NULL REFERENCES custom_actions(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    param_type  TEXT NOT NULL DEFAULT 'string',
    description TEXT NOT NULL DEFAULT '',
    required    INTEGER NOT NULL DEFAULT 0,
    maps_to     TEXT NOT NULL DEFAULT '',          -- DB column / API field the value maps to
    extra       TEXT NOT NULL DEFAULT '{}',        -- JSON: operator, location, choices, role
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (action_id, name)
);
CREATE INDEX IF NOT EXISTS idx_params_action ON action_parameters(action_id);

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT 'New conversation',
    test_mode  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_conversations_bot ON conversations(chatbot_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','notice')),
    content         TEXT NOT NULL DEFAULT '',
    tool_calls      TEXT NOT NULL DEFAULT '[]',     -- assistant: [{id,name,arguments}]
    tool_call_id    TEXT NOT NULL DEFAULT '',       -- tool: the call it answers
    meta            TEXT NOT NULL DEFAULT '{}',     -- UI details (status, summary, ...)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    token           TEXT PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    action_id       INTEGER REFERENCES custom_actions(id) ON DELETE SET NULL,
    tool_call_id    TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    arguments       TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','declined','expired')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_pending_conv ON pending_confirmations(conversation_id, status);

CREATE TABLE IF NOT EXISTS action_execution_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chatbot_id      INTEGER REFERENCES chatbots(id) ON DELETE SET NULL,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    action_id       INTEGER REFERENCES custom_actions(id) ON DELETE SET NULL,
    action_name     TEXT NOT NULL,
    arguments       TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL,                  -- ok | error | simulated | rejected | declined
    result_summary  TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    test_mode       INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'chat',   -- chat | manual_test
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_user ON action_execution_logs(user_id, id);
CREATE INDEX IF NOT EXISTS idx_logs_action ON action_execution_logs(action_id);

CREATE TABLE IF NOT EXISTS chatbot_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chatbot_id     INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    snapshot       TEXT NOT NULL,                   -- JSON of chatbot + settings
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (chatbot_id, version_number)
);

CREATE TABLE IF NOT EXISTS rasa_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    content        TEXT NOT NULL,                   -- original file text, preserved byte-for-byte
    content_sha256 TEXT NOT NULL,
    parse_ok       INTEGER NOT NULL DEFAULT 0,
    parse_error    TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_rasa_user ON rasa_imports(user_id);
