# E2E checklist (manual)

## macOS

The macOS Adapter is developed on Windows behind a platform seam and unit-tested with
injected fakes. Everything below can ONLY be verified on a real Mac (Apple Silicon and Intel,
macOS 11+). Tick each item and note the macOS version.

Build and package
- [ ] `build/build-mac.sh` finishes; `dist-mac/adapter-mac.zip` holds `TokenVault Adapter.app` and `Install.command` (mode 755).
- [ ] `codesign --verify --deep --strict` passes on the app; the app has no Dock icon (LSUIElement).
- [ ] Copy the zip to the Manager data folder `templates/adapter-mac.zip`. On Windows Manager: Installer page, target macOS, create. Output is `TokenVault-Adapter-<team>-mac.zip`.
- [ ] Without the template the Installer page shows the Vietnamese "build on a Mac" message.

Install (employee Mac)
- [ ] Unzip a downloaded copy (quarantined). Right-click `Install.command` -> Open works; a plain double-click is allowed or shows the Gatekeeper prompt (document which).
- [ ] The consent dialog (osascript) is short and readable; "Xem đầy đủ" opens the full text in TextEdit (all wire fields, "what is not sent") and the dialog comes back. Esc/Decline installs nothing (exit 3).
- [ ] Re-running `Install.command` on a Mac that already has the agent: decline leaves the old app, plist and agent untouched; accept replaces them; a failing setup restores the old app.
- [ ] After Accept: app is in `~/Applications`, `~/Library/LaunchAgents/vn.ics.tokenvault.adapter.plist` exists, `launchctl print gui/$(id -u)/vn.ics.tokenvault.adapter` shows it loaded.
- [ ] Keychain Access shows items for service `vn.ics.tokenvault.adapter`; no secret appears in `ps aux` during setup; `config.json` and the `.key`/`.secret` files hold no secret (only `TVK1:` markers).
- [ ] No Keychain permission prompt appears for the background agent (or document the prompt).

Run
- [ ] A menu-bar icon appears (pystray on the main thread) and "What is sent?" opens. If the icon cannot be shown the agent REFUSES to collect: notification appears, `status` shows no_indicator, exit code 0 and launchd does not respawn it in a loop.
- [ ] `launchctl bootout gui/$(id -u)/vn.ics.tokenvault.adapter` and `kill -TERM` stop the agent promptly while the menu-bar loop is running (sigwait thread).
- [ ] Data folder is `~/Library/Application Support/TokenVault`, log in `~/Library/Logs/TokenVault/adapter.log`.
- [ ] Use Claude Code; events reach the Manager (device appears with the team). FSEvents picks up new `.jsonl` lines within a few seconds.
- [ ] Idle CPU is near zero; `nice` shows 10+. Network to the Manager is not stalled by the background policy (`setpriority` PRIO_DARWIN_BG).
- [ ] Re-running `Install.command` while the agent runs reloads it (SIGUSR1) and starts exactly one agent; two simultaneous launches leave one (flock).
- [ ] Log out and in: the agent starts via the LaunchAgent. Kill -9 the agent: launchd restarts it (KeepAlive after a crash). Clean quit does not restart it.
- [ ] `Uninstall.command` (right-click -> Open) removes the app, the plist, the Keychain items, `~/Library/Application Support/TokenVault` and the logs, and the agent process ends.

## Automated E2E results

Harness: `tests/e2e/run_e2e.py` (run by hand: `.venv\Scripts\python tests\e2e\run_e2e.py`, about
3.5 min) and `pytest -m slow tests/e2e` (shorter idle and burst). Everything runs from source as
separate real OS processes: the Manager back end (`manager_host.py`: the real `lifecycle.Manager`
plus the app's token-protected `POST /api/<method>` server), a recording TLS proxy
(`wire_proxy.py`, which presents the Manager's own certificate so the pin passes) and one
`python -m tokenvault.adapter.main setup|run` per fake employee. Each employee has its own fake
USERPROFILE/HOME (with `~/.claude/projects` transcripts and `~/.claude.json`), LOCALAPPDATA (so the
adapter uses its default data dir), USERNAME (which sets the device id) and mutex name. The
ground truth is `ground_truth.py`. It does not import `tokenvault`: it has its own JSONL reader,
message-id de-duplication and cost math over the same `data/pricing.json`.

Last full run: 2026-11-19, Windows 11, 12 logical CPUs. The machine was shared with other agents'
builds and tests, so timings are pessimistic. All 9 steps PASS (and step 10 PASS).

| # | What was verified for real | Result |
|---|---|---|
| 1 | Manager back end starts in its own process with temp dirs, ingest on a non-default port, discovery on a non-default UDP port, fixed UTC+7. An API call with a wrong token is refused (403). | PASS, start 0.6 s |
| 2 | `create_installer` for Dev and QA. The payload read back from each produced file has the right port, fingerprint, team and hosts. `adapter setup --from <installer> --silent --accept --no-start --no-autostart` ran for 5 employees (3 Dev, 2 QA; one person on 2 devices; one with no account e-mail). `config.json` has no secret. After `run`: all devices online in the right team, `device.key` stored and `enroll.secret` deleted, package device counts are 3 and 2. | PASS, setup about 0.3 s each, all online 0.7 s |
| 3 | Independent ground truth vs the Manager API: overview totals (5 token kinds, events, sessions, cost, cost_partial, unknown model flagged), each UTC+7 day bucket (tokens + cost, including events on both sides of local midnight), by model, by project, each employee (tokens, events, sessions, cost, devices, teams, online, projects, models, daily), team totals, session list and titles (35), and sub-agent (sidechain) tokens. Sample data covers multi-line responses, a message id repeated across files, an unknown model, `<synthetic>`, a line without `message.id`, junk lines, a bad timestamp and a partial last line. | PASS: 277 events, 37 sessions, 13,632,687 tokens, $27.7125, converged 2.2 s after start |
| 4 | Latency from a transcript append until the event is visible via the API (20 samples) | PASS: median 0.66 s, p90 1.05 s, max 1.13 s. Completing a partial last line: 0.67 s |
| 5 | Manager killed (TerminateProcess) while 230 lines were written. Outboxes held the backlog (150 + 80 waiting). Manager restarted; then an adapter was killed mid-write (300 lines, some written in two pieces) and restarted. Full ground-truth comparison after each restart. DB rows equal unique messages; `PRAGMA quick_check` ok. | PASS: backlog in 2.9 s after Manager restart, 0.8 s after adapter restart, no duplicates or losses (829 = 829) |
| 6 | Revoke a device: its session is cut, later lines never land and it is hidden. Reinstall on the revoked device id: rejected, no key. Forged secret (valid format): rejected. A valid Dev secret claiming team QA lands in Dev. A Dev device re-enrolling with the QA secret: rejected and stays Dev. Revoke the QA package: both QA devices cut and a new QA install rejected. A certificate-pin impostor: 2 TCP connects and **0 application bytes**, adapter reports "fingerprint mismatch". | PASS |
| 7 | Privacy: a unique marker was placed in prompts, replies, thinking, tool inputs, tool results (file content), the full cwd path, git branch, summary lines, the encoded project folder name and `.claude.json` noise. It was searched for in every Manager data file (DB after a clean stop, logs, installers, reports), every adapter data dir, and all decoded wire frames (client frames unmasked) plus raw plaintext. | PASS: 0 hits in 82 files (19 MB) and 3.6 MB of wire traffic. Fields on the wire: account_uuid, token counts, claude_account_email, device_id, hostname, is_sidechain, message_id, model, project (last path segment), reasoning_effort, session_id, session_title, team, timestamp, tool_names, uuid, windows_user |
| 8 | PDF reports through `export_report`: all (30d), one person, one team. Parsed with pypdf. Total tokens, cost and sessions from the ground truth appear in the text. No marker and no session titles. | PASS: all 7 pages ("19.843.691", "$35.15+"), alice 2 pages, team QA 5 pages |
| 9 | Adapter process resources (Windows GetProcessTimes / working set) | PASS: idle 60 s = 0.09 CPU-s (0.16% of one core), RSS 37 MB (private 25 MB). Burst of 5,000 lines: all in the Manager after 2.9 s, 0.97 CPU-s, peak RSS 42 MB |
| 10 | `tests/e2e/real_claude_check.py`: read-only run of the real Collector over the owner's real `~/.claude/projects` (1,537 files, 4.0 GB) into a throw-away outbox, compared with the independent reader over the exact bytes the collector consumed. Nothing is sent and only aggregates are printed. | PASS: 61,585 messages in both, 0 missing or extra, 0 token mismatches, totals, per model, per day (24 days) and cost equal. 1 message has a different sidechain flag (same id written in a main and a sub-agent file; attribution only, tokens equal). 265 lines over 1 MiB were skipped by the collector, with no usage lost. |

Bug found and fixed during the run: watchdog's default observer timeout (1 s) made the Windows
emitter hold every change notification until 1 s of quiet. Appends reached the Manager after
about 1.6 s, and a file written continuously only reached the Manager through the 1 s hot-file
poll. `Watcher` now uses `Observer(timeout=0.1)`: median 1.58 s became 0.66 s, and idle CPU went
from about 0.1% to about 0.2-0.3% of one core. Regression tests are in `tests/unit/test_watcher.py`.

Findings that are not code bugs (owner decision):
- **Pricing (resolved 2026-11-19).** At the time of the run the models the owner uses today
  (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5`,
  `claude-fable-5-1`) were missing from `data/pricing.json` and showed cost UNKNOWN. They are now
  present, with rates verified on 2026-11-19 against the official pricing page (see
  `_comment_sources` in the file). Models that are still unlisted show UNKNOWN, never a guess.
- A session title written after the session's last response does not reach the Manager until the
  next response, because titles ride on events.
- The adapter's `state.json` (used by `status`) is written at most every 5 s, so a failure reason
  can show up to 5 s late.
- The installed adapter backfills only files modified in the last 90 days (by design). Older
  history is never sent.

Still needs two real machines and manual checks (not covered by the harness, which runs on
loopback on one PC):
- [ ] Manager PC and employee PC on the same LAN and on Wi-Fi (AP client isolation, guest
  networks): the employee installer reaches the Manager by the embedded LAN IPs. Discovery finds
  the Manager after its IP changes (DHCP).
- [ ] Windows Defender Firewall on the Manager: the inbound rule exists for the ingest port (and
  UDP discovery). The first-run prompt / elevation behaves as documented. Public vs private network
  profile.
- [ ] SmartScreen / Mark-of-the-Web on the downloaded unsigned installer exe ("More info -> Run
  anyway"). Application Control / AV blocking an unsigned exe (seen on this dev machine: frozen
  exes could not be run here at all, so only the source build was exercised).
- [ ] **Task 29, Manager installer without an unsigned exe** (needs a machine with Smart App
  Control / WDAC that blocks unsigned exes; NOT verified by the developer, who only compiled the
  Setup with ISCC, simulated the installed layout by file copy and ran the runtime's
  `-m tokenvault.manager --selftest --require-template`, `firewall --check` and
  `firewall --ensure --dry-run`):
  - [ ] `TokenVault-Manager-Setup.exe` starts and finishes (it is still an unsigned Inno stub; if
    even that is blocked, the portable ZIP is the fallback).
  - [ ] Start menu "TokenVault Manager" (target `{app}\runtime\pythonw.exe`, args
    `-m tokenvault.manager`, brand icon) opens the window; no 4551 error. Desktop shortcut task and
    the "start when I sign in" task create the same command (HKCU Run value ends in
    `--minimized`).
  - [ ] Firewall step: one UAC prompt (publisher "Python Software Foundation"), then
    `python.exe -m tokenvault.manager firewall --check` exits 0 and the rules have
    `program=<app>\runtime\pythonw.exe`. Decline the prompt on a second run: Setup finishes with the
    Vietnamese + English message and the exact command; Start menu "Mở cổng tường lửa cho
    TokenVault (cần quyền admin)" then creates the rules with one click.
  - [ ] Employee PC reaches the Manager (ingest TCP 47800, discovery UDP 47801) with the rule
    restricted to `pythonw.exe`.
  - [ ] Installer page: "Windows – gói không cần exe" is first and selected for a new install;
    the .exe option is labelled as possibly blocked by Smart App Control.
  - [ ] Upgrade over an old exe-based install removes `TokenVault-Manager.exe`/`_internal`;
    uninstall removes the shortcuts, the autostart value and (after UAC) the firewall rules; the
    Manager data folder is kept unless chosen.
- [ ] Real frozen `TokenVaultAdapter.exe` / Manager exe: consent window, tray icon, HKCU Run
  autostart after logoff/logon, upgrade by re-running a newer installer.
- [ ] Real Claude Code writing live transcripts while the adapter runs (the harness simulates the
  writes). Sleep/resume and a network cable pull with a long offline backlog.
- [ ] Manager GUI pages showing the same numbers as the API (the UI tests use seeded data).
