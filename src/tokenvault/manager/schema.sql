-- TokenVault Manager schema, version 1.
-- Token columns: input, output, cache_read, cache_5m, cache_1h (all integer counts).
-- Time: ts = UTC epoch seconds; hour = floor(ts / 3600); day = floor(ts / 86400) (UTC).
-- Cost is never stored; it is computed from token sums by Aggregates + Pricing.
-- Team and the fallback e-mail live on `devices` and are joined at query time.

CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    team         TEXT NOT NULL DEFAULT '',
    email        TEXT,
    windows_user TEXT NOT NULL DEFAULT '',
    hostname     TEXT NOT NULL DEFAULT '',
    last_seen    REAL NOT NULL DEFAULT 0
);

-- One row per assistant response, keyed by (device_id, message_id or uuid).
CREATE TABLE IF NOT EXISTS events (
    device_id        TEXT NOT NULL,
    msg_key          TEXT NOT NULL,
    uuid             TEXT NOT NULL,
    message_id       TEXT,
    session_id       TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    ts               REAL NOT NULL,
    hour             INTEGER NOT NULL,
    model            TEXT NOT NULL,      -- '' when the event had no model
    reasoning_effort TEXT,
    sidechain        INTEGER NOT NULL,
    input            INTEGER NOT NULL,
    output           INTEGER NOT NULL,
    cache_read       INTEGER NOT NULL,
    cache_5m         INTEGER NOT NULL,
    cache_1h         INTEGER NOT NULL,
    project          TEXT NOT NULL,
    session_title    TEXT,
    tool_names       TEXT NOT NULL,      -- JSON array, first-seen order, unique
    email            TEXT NOT NULL,      -- '' when the event had no account e-mail
    account_uuid     TEXT,
    windows_user     TEXT NOT NULL,
    hostname         TEXT NOT NULL,
    team_at_send     TEXT NOT NULL,
    PRIMARY KEY (device_id, msg_key)
);
CREATE INDEX IF NOT EXISTS ix_events_session ON events (session_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_hour ON events (hour);

CREATE TABLE IF NOT EXISTS rollup_hour (
    device_id  TEXT NOT NULL,
    email      TEXT NOT NULL,
    project    TEXT NOT NULL,
    model      TEXT NOT NULL,
    sidechain  INTEGER NOT NULL,
    hour       INTEGER NOT NULL,
    input      INTEGER NOT NULL DEFAULT 0,
    output     INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_5m   INTEGER NOT NULL DEFAULT 0,
    cache_1h   INTEGER NOT NULL DEFAULT 0,
    events     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, sidechain, hour)
);
CREATE INDEX IF NOT EXISTS ix_rh_hour ON rollup_hour (hour);
CREATE INDEX IF NOT EXISTS ix_rh_email_hour ON rollup_hour (email, hour);
CREATE INDEX IF NOT EXISTS ix_rh_project_hour ON rollup_hour (project, hour);

CREATE TABLE IF NOT EXISTS rollup_day (
    device_id  TEXT NOT NULL,
    email      TEXT NOT NULL,
    project    TEXT NOT NULL,
    model      TEXT NOT NULL,
    sidechain  INTEGER NOT NULL,
    day        INTEGER NOT NULL,
    input      INTEGER NOT NULL DEFAULT 0,
    output     INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_5m   INTEGER NOT NULL DEFAULT 0,
    cache_1h   INTEGER NOT NULL DEFAULT 0,
    events     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, sidechain, day)
);
CREATE INDEX IF NOT EXISTS ix_rd_day ON rollup_day (day);
CREATE INDEX IF NOT EXISTS ix_rd_email_day ON rollup_day (email, day);

-- Number of responses that used each tool (tool_names are unique per response).
CREATE TABLE IF NOT EXISTS rollup_tool_hour (
    device_id TEXT NOT NULL,
    email     TEXT NOT NULL,
    project   TEXT NOT NULL,
    model     TEXT NOT NULL,
    hour      INTEGER NOT NULL,
    tool      TEXT NOT NULL,
    calls     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, hour, tool)
);
CREATE INDEX IF NOT EXISTS ix_rt_hour ON rollup_tool_hour (hour);
CREATE INDEX IF NOT EXISTS ix_rt_tool_hour ON rollup_tool_hour (tool, hour);

-- Per-session totals split by (email, project, model) so cost and filters stay exact.
CREATE TABLE IF NOT EXISTS rollup_session (
    device_id        TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    email            TEXT NOT NULL,
    project          TEXT NOT NULL,
    model            TEXT NOT NULL,
    input            INTEGER NOT NULL DEFAULT 0,
    output           INTEGER NOT NULL DEFAULT 0,
    cache_read       INTEGER NOT NULL DEFAULT 0,
    cache_5m         INTEGER NOT NULL DEFAULT 0,
    cache_1h         INTEGER NOT NULL DEFAULT 0,
    sidechain_tokens INTEGER NOT NULL DEFAULT 0,
    events           INTEGER NOT NULL DEFAULT 0,
    first_ts         REAL NOT NULL,
    last_ts          REAL NOT NULL,
    PRIMARY KEY (device_id, session_id, email, project, model)
);
CREATE INDEX IF NOT EXISTS ix_rs_last ON rollup_session (last_ts);
CREATE INDEX IF NOT EXISTS ix_rs_session ON rollup_session (session_id);

-- Latest non-null title received for a session.
CREATE TABLE IF NOT EXISTS session_meta (
    device_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    PRIMARY KEY (device_id, session_id)
);
