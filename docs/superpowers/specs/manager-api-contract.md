# Manager API contract (UI <-> Python)

Binding for Task 12 (implements `manager/api.py`) and Tasks 14-17 (UI consumers).
All methods are exposed to JS as `window.pywebview.api.<name>(...)` and return JSON-serialisable
values (dataclasses via `dataclasses.asdict`). On failure they raise (JS gets a rejected promise);
the UI shows an error banner with a retry button. Costs: `null` = unknown, never 0.

## Filter (dict, all keys optional)
`{team, email, project, model, start, end}` — `email` is the *employee key* (`EmployeeRow.key`,
which is `"device:<id>"` for devices without a Claude account), `start`/`end` are date-only
`YYYY-MM-DD` local days.

## Read methods (shapes = dataclasses in `manager/agg_models.py`)
- `overview(filter) -> Overview` · `employees(filter) -> EmployeeRow[]`
- `employee(key, range) -> EmployeeView` (`range` in `"today"|"7d"|"30d"`)
- `projects(filter) -> ProjectRow[]` · `sessions(filter) -> SessionRow[]`
- `session(session_id) -> SessionRow | null` (single session header; Task 12 builds it via a new `Aggregates.session()`; the UI uses it instead of scanning `sessions()`)
- `session_turns(session_id) -> SessionTurn[]` (ordered by timestamp)
- `skills(range) -> SkillsView` · `orchestration(range) -> OrchestrationView`
- `teams() -> [{name, devices, online}]` (team names come from devices; `"(no team)"` allowed)

## Management methods (Tasks 12/17)
- `devices() -> [{device_id, email, display_name, windows_user, hostname, team, online, last_seen, package_id}]`
- `set_device_team(device_id, team) -> {ok}` · `revoke_device(device_id) -> {ok}` (closes live session)
- `reset_device(device_id) -> {ok}` (owner action: drops the device's key and closes its live session so the real machine may enroll ONE more time; a device that already holds a key can never be re-enrolled otherwise; `not_found` if unknown or revoked)
- `packages() -> [{package_id, team, label, created_at, revoked, devices}]` · `revoke_package(package_id) -> {ok}`
- `create_installer(team, label|null) -> {path, file_name, team, bytes}` (raises if template missing)
- `reveal_in_explorer(path) -> {ok}`
- `settings() -> {transparent_window: bool, start_with_windows: bool, port: int, manager_id: str, hosts: [str] (RAW configured list; empty = automatic), hosts_auto: [str] (detected), installer_hosts: [str] (what goes into new installers), window_shape: str, fingerprint: str, static_ip_hint: str, tz_offset_minutes: int, version: str}`
- `save_settings(patch) -> settings` (allowed keys: transparent_window, start_with_windows, hosts, window_shape)
- `diagnostics() -> {port, port_requested, fingerprint, connected: [device_id], connected_count, last_event_at|null, rejected_count, firewall_rule_present: bool, cert_expires_at, db_path, queue: {events, devices}, warnings: [str]}`
- `connectivity_test() -> {ok, checks: [{name, ok, detail}]}` (bind, firewall rule, LAN IPs, certificate)
- `regenerate_certificate() -> {fingerprint, warning}` (explicit; invalidates all installed adapters)

## Report export methods (Task 22)
- `report_options() -> {default_dir, can_pick_path, can_open, can_reveal, teams: [str], max_employees, page_cap, ranges}` (`default_dir` = `Documents\TokenVault Reports`)
- `export_report(spec) -> {path, file_name, folder, pages, bytes, scope_label, range_label}` where
  `spec = {scope: "all"|"team"|"employee", employee_keys?: [str], team?: str, range: "today"|"7d"|"30d"|"custom", start?, end?, per_employee_pages?: bool, destination?: "default"|"dialog"}`.
  The UI never sends a path: `default` writes a generated, sanitised file name into the reports folder, `dialog` opens the native save dialog (Python side). Session titles are never exported. Runs synchronously on its own worker thread; one export at a time.
- `report_progress() -> {running, fraction (0..1), message}` (polled by the dialog while `export_report` is pending)
- `open_path(path, reveal=false) -> {ok}` opens (or, with `reveal`, selects in Explorer) a `.pdf` inside the reports folder or one this process exported; anything else is refused.
- Error prefixes: `[invalid_args]`, `[invalid_report_spec]`, `[empty_scope]` (unknown team/employee), `[font_missing]`, `[report_failed]`, `[write_failed]`, `[busy]`, `[cancelled]` (save dialog dismissed), `[not_found]`.

## Window methods (owner request: a normal resizable desktop window)
The window is frameless + chamfered (CSS) but must behave like a normal app window: resizable from every edge/corner,
minimise, maximise/restore, full screen, remembers its size/position. All geometry is in the SAME logical (DPI-scaled)
pixels the page sees (`window.innerWidth`); Python converts. Minimum size 1100x720.
- `window_min()`, `window_close()` (hide to tray), `window_ready()`
- `window_toggle_maximize() -> WindowState` · `window_maximize()` · `window_restore()` — maximise = the monitor's WORK AREA
  (never covers the taskbar), restore returns to the saved normal bounds
- `window_set_fullscreen(on: bool) -> WindowState` · `window_toggle_fullscreen() -> WindowState` — full screen = the whole monitor, no taskbar
- `window_state() -> WindowState` where `WindowState = {native_region: bool, native_resize: bool, maximized: bool, fullscreen: bool, x, y, width, height, min_width, min_height, screen: {x, y, width, height, work_x, work_y, work_width, work_height}}`
- `window_get_bounds() -> {x, y, width, height}` · `window_set_bounds(x, y, width, height) -> WindowState` (clamped to min size and to the visible screen area; ignored while maximised/fullscreen except it first restores)
- `window_start_move()` is NOT needed: the title bar uses the native drag region; double-click on the title bar toggles maximise (UI).
- Edge/corner resizing: native if achievable (WS_THICKFRAME / WM_NCHITTEST on the HWND); otherwise the UI draws 6 px invisible
  resize handles and drives `window_get_bounds()` + `window_set_bounds()` during the pointer drag. Python must support both.
- Python pushes `tv.onWindowState(<WindowState json>)` on every change (maximise, restore, fullscreen, resize, move; throttled to ~10/s with a
  trailing flush); the UI sets `.window--maximized` / `.window--fullscreen` on the root and hides the chamfer/margins in those states.
- Persistence: the Manager saves `{x, y, width, height, maximized}` (NOT fullscreen) in settings.json on change (debounced) and restores it at start,
  validating against the current monitors (off-screen/too-large saved geometry falls back to centred 1440x900 or the work area).

## Live push
Python calls `tv.onLive(<json>)` at most 2/s with `{online, total, last_event_at, devices:{online,total}}`;
pages refetch what they show (debounced).

## Time zones
The Manager sets `Store(tz_offset_minutes=<Windows local offset at start>)`; the pywebview UI runs on the SAME
machine, so browser-local time == the server's bucketing offset. Day/hour *bucket strings* in series come from the
server and must be used as-is for axes. Instants (`SessionTurn.timestamp`, `last_event_at`, `last_seen`) are ISO-8601
UTC (`...Z`/offset) and the UI converts them to browser-local time for display. Pages must not compute their own
buckets from the browser clock except for the *current* day/hour used for range bounds.

## Error classification
Errors the UI must react to carry a machine-readable prefix in the exception message:
`[template_missing]`, `[cert_error]`, `[revoked]`, `[invalid_team]`, and the report prefixes above. The UI tests for the prefix, never for words.
