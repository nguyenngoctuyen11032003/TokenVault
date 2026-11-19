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
- `session_turns(session_id) -> SessionTurn[]` (ordered by timestamp)
- `skills(range) -> SkillsView` · `orchestration(range) -> OrchestrationView`
- `teams() -> [{name, devices, online}]` (team names come from devices; `"(no team)"` allowed)

## Management methods (Tasks 12/17)
- `devices() -> [{device_id, email, display_name, windows_user, hostname, team, online, last_seen, package_id}]`
- `set_device_team(device_id, team) -> {ok}` · `revoke_device(device_id) -> {ok}` (closes live session)
- `packages() -> [{package_id, team, label, created_at, revoked, devices}]` · `revoke_package(package_id) -> {ok}`
- `create_installer(team, label|null) -> {path, file_name, team, bytes}` (raises if template missing)
- `reveal_in_explorer(path) -> {ok}`
- `settings() -> {transparent_window: bool, start_with_windows: bool, port: int, manager_id: str, hosts: [str], fingerprint: str, static_ip_hint: str, tz_offset_minutes: int, version: str}`
- `save_settings(patch) -> settings` (allowed keys: transparent_window, start_with_windows, hosts)
- `diagnostics() -> {port, port_requested, fingerprint, connected: [device_id], connected_count, last_event_at|null, rejected_count, firewall_rule_present: bool, cert_expires_at, db_path, queue: {events, devices}, warnings: [str]}`
- `connectivity_test() -> {ok, checks: [{name, ok, detail}]}` (bind, firewall rule, LAN IPs, certificate)
- `regenerate_certificate() -> {fingerprint, warning}` (explicit; invalidates all installed adapters)

## Window methods
`window_min()`, `window_close()` (hide to tray), `window_ready()`.

## Live push
Python calls `tv.onLive(<json>)` at most 2/s with `{online, total, last_event_at, devices:{online,total}}`;
pages refetch what they show (debounced).
