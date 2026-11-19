# Employee guide (Adapter)

TokenVault Adapter is a small background program that reports **how many tokens** Claude Code used to your
manager's PC on the company network. It does **not** read or send your conversations. See [privacy.md](privacy.md).

## 1. Install
1. Get `TokenVault-Adapter-<Team>.exe` from your manager (network share or USB).
2. Double-click it. **Windows SmartScreen** may say "Windows protected your PC" because the file has no paid
   code-signing certificate: click **More info** -> **Run anyway**. (If you downloaded it in a browser: right-click
   -> Properties -> tick **Unblock** -> OK.)
3. A consent screen lists exactly what is sent. Read it and accept.
4. A TokenVault tray icon appears (bottom right) and starts at every login. No admin rights needed.

If your PC blocks unknown .exe files, ask your manager for the "no-exe" package (**in progress, will be updated
later**). On Mac a ZIP with `Install.command` is used (**in progress, will be updated later**; macOS may ask you to
use System Settings -> Privacy & Security -> "Open Anyway").

## 2. What is sent?
Per Claude response: token counts (input/output/cache), model, time, session id, **only the last folder name** of the
project, session title, the **names** of tools called, Claude account email, Windows user name, computer name, team.
**Never**: prompts, replies, source code, file contents, commands, full paths, passwords or keys. Full list:
[privacy.md](privacy.md).

## 3. Check it yourself
* Right-click the tray icon -> **What is sent?** shows a real queued event.
* The first menu line shows connection state and queue size.
* Command line: `TokenVaultAdapter.exe status --json` (add `--lang en`).
* Logs: tray menu -> "Open log folder" (`%LOCALAPPDATA%\TokenVault\Adapter\adapter.log`, never contains keys or secrets).

There is deliberately **no pause or hide button**; the Adapter is always visible.

## 4. Uninstall
Windows Settings -> **Apps** -> "TokenVault Adapter" -> Uninstall. It stops immediately; the Manager shows the
device as offline.

## 5. FAQ
* **Does it slow my PC?** No: about 0% CPU idle, under 40 MB RAM, low priority, only watches `~/.claude/projects`.
* **Away from the office network?** Events queue locally and are sent when you are back.
* **Can my manager see what I typed?** No.
* **No Claude account (API key)?** Shown as "Unknown account (user@host)".
* **Is old history sent on first install?** Yes: existing logs are read in the background, rate limited, and marked historical.
* **Tray icon missing?** Check the hidden-icons arrow on the taskbar; if still missing, tell your manager.
