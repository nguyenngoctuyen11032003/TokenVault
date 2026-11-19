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

**If your PC blocks unknown .exe files outright** (no "Run anyway" button), ask your manager for the **no-exe
package** (`TokenVault-Adapter-<Team>-portable.zip`): extract the whole ZIP to a folder, double-click `Install.cmd`
(no admin rights; it removes the "downloaded from the internet" mark itself), read the consent screen and accept.
Remove it with `Uninstall.cmd` from the same folder.

**On a Mac:** get `TokenVault-Adapter-<Team>-mac.zip`, double-click to unzip, right-click `Install.command` ->
**Open** (macOS 15: if that is not offered, System Settings -> Privacy & Security -> "Open Anyway"). The consent
dialog appears before anything is installed; declining changes nothing. Afterwards the icon lives in the **menu
bar** (mandatory, cannot be hidden; if it cannot be shown the agent stops collecting and tells you with a
notification), secrets are kept in the Keychain, and it starts at login. Remove it with `Uninstall.command`
(right-click -> Open). The Mac build has not been verified on a real Mac yet.

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
exe installer: Windows Settings -> **Apps** -> "TokenVault Adapter" -> Uninstall. No-exe package: `Uninstall.cmd`
(add `-RemoveData` to delete collected data too). Mac: `Uninstall.command`. It stops immediately; the Manager shows
the device as offline.

## 5. FAQ
* **Does it slow my PC?** No: about 0% CPU idle, under 40 MB RAM, low priority, only watches `~/.claude/projects`.
* **Away from the office network?** Events queue locally and are sent when you are back.
* **Can my manager see what I typed?** No.
* **No Claude account (API key)?** Shown as "Unknown account (user@host)".
* **Is old history sent on first install?** Yes: existing logs are read in the background, rate limited, and marked historical.
* **Mac: no menu-bar icon?** The agent deliberately does not collect without a visible icon; tell your manager (state `no_indicator` in `status`).
* **Tray icon missing?** Check the hidden-icons arrow on the taskbar; if still missing, tell your manager.
