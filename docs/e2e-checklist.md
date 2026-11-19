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
