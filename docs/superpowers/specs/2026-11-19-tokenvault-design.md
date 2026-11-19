# TokenVault — Design

Date: 2026-11-19 · Status: draft for approval · Platform: Windows 10/11 · Language: Python 3.11+

## 1. Intent and why

ICS staff use Claude Code. The owner (a manager) wants to see, in near real time,
**which Claude account used how many tokens, on which project, in which session, at what cost** —
per employee and per team — without buying a server or a cloud service.

Deliverables are exactly **two installers**:

1. **TokenVault Manager** — a desktop app the manager installs on one PC. It receives data,
   shows the dashboard, and has a "Create employee installer" function.
2. **TokenVault Adapter** — a small background agent installed on employee PCs from an
   `.exe` that the Manager generated. It reads Claude Code's local logs and reports usage to the Manager.

Reference material (read, partly reused): `auh-manager-windows-0.1.0`, `auh-adapter-windows-0.1.0`.
That reference is CLI/browser based, reports hourly, needs openssl/firewall/manual tokens, and
deliberately omits project and account. TokenVault replaces those traits; it keeps the good parts (see §5.4).

## 2. Goals / non-goals / anti-goals

**Goals**
- Manager sees every installed employee, identified by **Claude account email** (e.g. `tuyen.nguyen@ics.vn`), filterable by team, employee, project, model, date range.
- Data reaches the Manager within ~1–2 s of Claude finishing a response while both are on the company LAN/Wi-Fi.
- Employee install = run one `.exe`, click through a consent screen. No typing of addresses, tokens, certificates.
- Adapter is invisible in performance terms (budget in §6).
- Free: no cloud, no paid service, no paid certificate.
- Professional, maintainable code structure (§7); UI in the Acer NitroSense style the owner approved (§4).

**Non-goals (v1)**
- Remote / home / internet access. One Manager PC, company network only.
- Multiple Manager PCs or per-team-lead logins / permissions. Single viewer.
- Claude.ai web chat or Claude Desktop chat usage (only Claude Code logs).
- macOS / Linux.
- Billing-grade accuracy. Totals are "as reported by local logs"; cost is an estimate from `pricing.json`.
- Auto-update (manual reinstall in v1).

**Anti-goals (a failure even if it "works")**
- Sending prompt text, Claude replies, file contents, commands, or full directory paths.
- Hidden operation: the employee must be told what is collected (consent screen, tray icon, `status` view).
- Noticeable CPU / disk / battery / network impact on employee machines.
- Manager needing openssl, firewall commands, or a server admin to get started.

## 3. What is collected

Per assistant response (one row, **keyed by `message_id`**, idempotent; fall back to `uuid` only if `message_id` is missing).
Measured on 30 real transcripts: 3,813 of 6,693 `assistant` lines repeat an earlier `message_id` with identical usage
(one API response is logged once per content block). Keying on `uuid` would overcount tokens ~2.3×. The parser keeps one row per
`message_id` (if usage ever differs, keep the larger value) and the Manager upserts on `(device_id, message_id)`:

| Field | Source |
|---|---|
| uuid, session_id, message_id, timestamp | transcript line |
| model, reasoning_effort, is_sidechain (sub-agent) | transcript line |
| input / output / cache_read / cache_create_5m / cache_create_1h tokens | `message.usage` |
| project | **last folder name** of `cwd` (not the full path) |
| session_title | transcript `ai-title` record for that session |
| tool_names | names of `tool_use` blocks in that response (names only, no inputs) |
| claude_account_email, account_uuid | `~/.claude.json` → `oauthAccount` |
| windows_user, hostname, team, device_id | adapter / installer |

Not sent: anything in message text, tool inputs/outputs, file paths beyond the last folder name.
Employee can run `status --json` / tray "What is sent?" to see a real queued event verbatim.

Verified on a real transcript on the owner's machine: records of type `assistant`, `ai-title`
exist; fields `cwd`, `sessionId`, `isSidechain`, `effort`, `tool_use` names exist; email is in `~/.claude.json`.
Builder must still verify against several Claude Code versions (field names drift).

## 4. UI / UX (approved direction)

Style: Acer NitroSense — frameless window with chamfered (cut) corners, near-black scratched/hatched
background, red (#e10b1f / #ff2d3f) accent, angled panel tabs, radial gauges with tick marks,
dark red grid charts, orange numeric highlights. Custom title bar (min / close, "live" indicator with
online-device count). Mockups: `.superpowers/brainstorm/*/content/manager-layout.html` (options A and B).

**Chosen layout: A + B combined.**
- **Home (A):** single-screen cockpit. Left: team list (All / Dev / QA / …). Center: three radial gauges for today (tokens, estimated cost, sessions) plus live tokens-per-hour line. Below: employee table (account email, online dot, tokens, share bar) and top projects. Click an employee → detail page.
- **Detail pages (B):** left nav — Overview, Employees, Projects, Sessions, Skills, Orchestration, Create Installer, Settings. Employee detail: range switch (today / 7d / 30d), gauge, per-project breakdown, session list (title, project, model, tokens, cost), device info and online state.
- **Charts to include (parity with auh dashboard, restyled):** daily tokens (line), daily cache reads, tokens by project (bar), tokens by model (donut), top tools by call count (bar), recent sessions, session turn-by-turn, orchestration (main vs sub-agent tokens, model split, agents by tokens), top skills by invocations with 7/30/90/all range. Charts are ECharts (SVG), reusing auh's chart helpers where licence/structure allow. Unknown-model cost shows UNKNOWN, never guessed.
- Global filters: team, employee (by email), project, model, date range. Live updates push into the open view without resetting scroll/selection.
- Title bar "Create installer" shortcut opens the installer wizard.
- Vietnamese UI text by default (Vietnamese labels as in mockups); strings kept in one resource file so English can be added.

## 5. Architecture

### 5.1 Stack (decided: pywebview shell)
- Manager: Python core + **pywebview** (WebView2 on Windows 10/11) hosting an HTML/CSS/JS UI. Frameless, transparent window; chamfered shape via CSS `clip-path`.
  Fallback if transparency misbehaves on some PCs: opaque rectangular window with the chamfer drawn inside it.
- Adapter: pure Python, no GUI toolkit, background process + tray icon (pystray) + small status window on demand.
- Local store: SQLite (WAL) on both sides. Packaging: PyInstaller + Inno Setup.

### 5.2 Transport (decided: LAN WebSocket, Manager as server)
- Manager runs an ingest server on a fixed port (default 47800), WebSocket over TLS.
- TLS uses a **self-signed certificate generated by the Manager on first run** (no openssl; `cryptography` library). Its SHA-256 **fingerprint is baked into each employee installer** and the adapter pins it. No CA, no cafile handling.
- Auth: each employee installer carries an **enrollment secret** (per team package). First connect registers the device and the Manager issues a per-device key; later connects use the device key. Manager can revoke a device or a whole package.
- Adapter keeps one persistent connection; sends small batches (debounced ≤1 s, ≤N events). Receipts acknowledge events; unacknowledged events stay in the local outbox. Idempotent by `uuid` — no duplicates.
- Manager offline / employee off-network: events queue locally, flush on reconnect (backoff, jittered).
- Manager discovery: installer carries Manager IP + hostname; adapter also remembers last good address and, if unreachable, probes the LAN with a UDP broadcast ("who is TokenVault Manager <id>?"). Recommendation shown in Manager Settings: give the Manager PC a static/reserved IP.
- Manager installer opens its firewall port itself (one UAC prompt at install).
- Wired and Wi-Fi may be different subnets: Manager shows a connectivity self-test and a clear message if devices cannot be reached.

### 5.3 Creating the employee installer
Manager ships a prebuilt `adapter-setup-template.exe`. "Create installer" asks for team name (and optionally a label), then writes a copy with an appended config payload (magic footer + JSON): Manager address(es), port, cert fingerprint, enrollment secret, team. The payload is not "signed": the trust anchor is the pinned cert fingerprint (adapter only talks to the right Manager) plus the enrollment secret (Manager only accepts devices that hold it; a forged installer cannot enroll). Output: `TokenVault-Adapter-<Team>.exe` ready to send by chat/email/USB.
Employee flow: run → consent screen listing exactly what is sent → install → tray icon, starts at login.
Unsigned exe will trigger SmartScreen; install guide says so (code signing is a paid extra, out of scope).

### 5.4 Reused from the auh reference (keep the idea, rewrite to fit)
- Incremental read of `~/.claude/projects/**/*.jsonl` by saved byte offset plus a prefix hash (detect rewritten/rotated files); skip over oversized lines and report them instead of silently under-reporting.
- Durable SQLite outbox + receipts, no duplicate sends.
- `pricing.json` with explicit UNKNOWN cost for unpriced models.
- Metadata-only parsing with validation and size limits.
Not reused: hourly CLI scheduling, openssl/cafile flow, manual token entry, "no project/account" restriction.

### 5.5 Module layout (single repo, shared core)
```
TokenVault/
  pyproject.toml
  src/tokenvault/
    common/      protocol.py  models.py  pricing.py  validation.py  config_payload.py
    adapter/     watcher.py  parser.py  identity.py  outbox.py  transport.py
                 discovery.py  tray.py  autostart.py  main.py
    manager/     app.py  ingest_server.py  store.py  aggregates.py  api.py
                 enrollment.py  tls.py  packager.py  discovery.py
    ui/          index.html  css/  js/ (routes, charts, components)  i18n/
  packaging/     manager.spec  adapter.spec  *.iss
  data/          pricing.json
  tests/         unit/  integration/
  docs/
```
Boundaries: `common` has no I/O. `adapter` never imports `manager`. UI talks to Python only through `manager/api.py`. Parser, outbox, protocol and aggregates are independently testable.

## 6. Constraints

- **Adapter budget:** ~0% CPU idle; <1% average while Claude is active; <40 MB RAM; no polling loop (OS file-change notification + offset tail, debounced); process priority below-normal; no disk scans beyond `~/.claude/projects`; network only to the Manager.
- First install **backfills history** (existing logs) in the background, rate-limited, marked historical so charts can separate it. (Builder may bound it, e.g. last 90 days — see §9.)
- Manager must stay responsive with ~50 devices and years of rows: SQLite indexes + pre-aggregated hourly/daily rollups; UI queries rollups, not raw rows.
- Windows 10 21H2+/11; WebView2 runtime required (bundled bootstrapper in Manager installer).
- All text data UTF-8; Vietnamese names/titles must render.

## 7. Quality bar
- Layered, typed (`mypy`-clean target), `ruff`-formatted, no giant files (auh's 1500-line `db.py` is the anti-example).
- Tests: parser (fixtures from real transcripts, anonymised), outbox/receipt/dup behaviour, protocol validation, aggregation maths, and an integration test adapter→manager over loopback including offline-then-reconnect.
- Structured logging to a rotating file on both sides; Manager has a Diagnostics page (connected devices, last event time, queue depth, rejected events).
- README for Manager install, employee guide (Vietnamese + English), and privacy statement matching §3.

## 8. Approaches considered
| Option | Trade-off | Outcome |
|---|---|---|
| PySide6/Qt widgets or QML | perfect custom window, pure Python; but must rewrite all charts, effects hard to tune, 80–120 MB | rejected |
| **pywebview + HTML/CSS/JS** | NitroSense look easy, reuses ECharts/auh chart ideas, mockups already HTML; transparency quirk has fallback | **chosen by owner** |
| Local web server + browser | simplest; not a real desktop app, no custom window | rejected |
Transport options (cloud relay vs LAN server): LAN WebSocket chosen because the owner requires free, simple, and all staff work on-site.

## 9. Left to the builder
- Exact wire schema/versioning, DB schema and rollup design, retry/backoff numbers, port fallback if 47800 is taken.
- Choice of file-watching library, tray library, installer scripting details, PyInstaller layout.
- How to extract per-session title robustly (`ai-title` records may repeat/change; use latest) and tool names across Claude Code versions.
- History backfill bound; retention/pruning policy (suggested: keep raw 12 months, rollups indefinitely).
- Phasing: deliver Home + Employee detail + Create Installer first; then Projects/Sessions; then Skills/Orchestration (the wire format must already carry `is_sidechain` and `tool_names` so these need no protocol change).
- Visual polish (gauges, hatch texture, motion) within the §4 style; accessibility (contrast, keyboard nav).

## 10. Calls made by the designer (not yet confirmed by the owner)
- Project = last folder name of `cwd`; full paths never sent.
- Self-signed cert with pinned fingerprint instead of CA/openssl.
- Enrollment secret per team package, per-device key issued on first connect.
- Tool **names** (not inputs) are collected to power "Top tools" and "Skills".
- Consent screen at install + tray icon (no stealth mode).
- Phased delivery order in §9; Skills/Orchestration pages last.
- Owner-approved: **no pause button** in the adapter. It stays visible (tray icon, "What is sent?" view) and is removed via Windows Apps & Features; the Manager shows the device as offline/disconnected afterwards.
- Single Manager PC; unsigned installers (SmartScreen warning documented).
- Adapter installs **per user, no admin rights** (`%LOCALAPPDATA%`, HKCU Run); one instance and one outbox per Windows user; `device_id` = machine + Windows user. Only the Manager installer needs UAC (firewall rule).
- Manager runs from the **system tray**: closing the window hides it, ingest keeps running in a background thread; optional start-at-login. Data arrives only while the Manager PC is on.
- Identity: the entity is the **Claude account email**; devices are listed under it and merged in views. Team is stored per device and joined at query time, so reassigning a device's team in the Manager applies retroactively.
- No `oauthAccount` (API-key/Bedrock/not logged in): shown as "Unknown account (windows_user@host)", never dropped.
- Cost is labelled **"API-equivalent estimate"** (subscription users do not pay per token). Only raw tokens are stored; cost is computed at query time from `pricing.json` (including 5m/1h cache rates), so a price change re-prices history.
- Build assumes a Windows dev PC with Python, PyInstaller and Inno Setup; build order adapter → template → Manager, scripted in `packaging/`. Manager keeps its cert/secrets under `%APPDATA%` protected with DPAPI.
