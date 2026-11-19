-- TokenVault Manager schema, version 2 (fresh databases; store.py migrates v1 -> v2).
-- Token columns: input, output, cache_read, cache_5m, cache_1h (all integer counts).
-- Time: ts = UTC epoch seconds; hour = floor(ts / 3600) (UTC hour number).
-- Local buckets use the store's fixed offset (meta 'tz_offset_minutes', no DST):
--   day   = floor((hour * 60 + offset) / 1440)     (local day number since 1970-01-01)
--   month = local year * 12 + (local month - 1)
-- An hour belongs to the local day its start falls in. Local tables are rebuilt
-- from the hourly ones when the offset changes.
-- Cost is never stored; it is computed from token sums by Aggregates + Pricing.
-- Team lives on `devices` and is joined at query time (retroactive).
-- E-mail identity is frozen at ingest: events.email = event e-mail, else the
-- device e-mail at that moment, else '' (no account).

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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
    email            TEXT NOT NULL,      -- frozen identity ('' = no account)
    account_uuid     TEXT,
    windows_user     TEXT NOT NULL,
    hostname         TEXT NOT NULL,
    team_at_send     TEXT NOT NULL,
    account_email    TEXT,               -- raw e-mail sent with the event
    title_ts         REAL,               -- event ts of the copy that carried the title
    PRIMARY KEY (device_id, msg_key)
);
CREATE INDEX IF NOT EXISTS ix_events_session ON events (session_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_hour ON events (hour);

-- Usage rollups: same dimensions, different time grain.
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
    day        INTEGER NOT NULL,           -- local day (store offset)
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

CREATE TABLE IF NOT EXISTS rollup_month (
    device_id  TEXT NOT NULL,
    email      TEXT NOT NULL,
    project    TEXT NOT NULL,
    model      TEXT NOT NULL,
    sidechain  INTEGER NOT NULL,
    month      INTEGER NOT NULL,           -- local year*12 + month-1
    input      INTEGER NOT NULL DEFAULT 0,
    output     INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_5m   INTEGER NOT NULL DEFAULT 0,
    cache_1h   INTEGER NOT NULL DEFAULT 0,
    events     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, sidechain, month)
);
CREATE INDEX IF NOT EXISTS ix_rm_month ON rollup_month (month);

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

CREATE TABLE IF NOT EXISTS rollup_tool_day (
    device_id TEXT NOT NULL,
    email     TEXT NOT NULL,
    project   TEXT NOT NULL,
    model     TEXT NOT NULL,
    day       INTEGER NOT NULL,
    tool      TEXT NOT NULL,
    calls     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, day, tool)
);
CREATE INDEX IF NOT EXISTS ix_rtd_day ON rollup_tool_day (day);
CREATE INDEX IF NOT EXISTS ix_rtd_tool_day ON rollup_tool_day (tool, day);

CREATE TABLE IF NOT EXISTS rollup_tool_month (
    device_id TEXT NOT NULL,
    email     TEXT NOT NULL,
    project   TEXT NOT NULL,
    model     TEXT NOT NULL,
    month     INTEGER NOT NULL,
    tool      TEXT NOT NULL,
    calls     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, email, project, model, month, tool)
);
CREATE INDEX IF NOT EXISTS ix_rtm_month ON rollup_tool_month (month);
CREATE INDEX IF NOT EXISTS ix_rtm_tool ON rollup_tool_month (tool, month, calls);

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
-- covering index for per-project session / employee counts
CREATE INDEX IF NOT EXISTS ix_rs_project
    ON rollup_session (project, device_id, session_id, last_ts, email);

-- Session title carried by the event with the highest timestamp.
CREATE TABLE IF NOT EXISTS session_meta (
    device_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    title_ts   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, session_id)
);
