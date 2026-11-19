# Architecture

## Modules (`src/tokenvault`)
| Package | Role |
|---|---|
| `common` | Pure, no I/O: `models.py` (`UsageEvent`), `protocol.py` (wire v1), `discovery_wire.py`, `config_payload.py` (TVLTCFG1), `pricing.py`, `dpapi.py` |
| `adapter` | `watcher` (file notifications) -> `collector`/`parser` (incremental tail by byte offset + prefix hash) -> `outbox` (SQLite, receipts) -> `transport` (WebSocket client, backoff) ; `discovery`, `identity`, `consent`, `tray`, `autostart`, `singleton`, `main` (`run`, `setup`, `status`, `about`, `show`) |
| `manager` | `ingest_server` + `ingest_session` (WebSocket/TLS), `enrollment` (packages, device keys), `store` + `agg_*` (SQLite, rollups), `api*` (UI contract), `packager` (installer payload), `tls`, `secrets` (DPAPI), `firewall`, `discovery` (UDP responder), `gui`/`window_*`/`tray`, `reports` (PDF) |
| `ui` | HTML/CSS/JS shell hosted by pywebview; talks to Python only through `manager/api.py` (contract: `docs/superpowers/specs/manager-api-contract.md`) |

Rules: `adapter` never imports `manager`; `common` has no I/O.

## Data flow
1. Claude Code appends `~/.claude/projects/**/*.jsonl` (or `$CLAUDE_CONFIG_DIR`).
2. The watcher wakes the collector, which reads new bytes, keeps one row per `message_id` (larger usage wins) and skips lines over 1 MiB (reported, not silently dropped).
3. Events go to the durable outbox, then to the Manager in batches (at most 200 events, frames at most 256 KiB).
4. The Manager validates, upserts on `(device_id, message_id)`, updates rollups, replies with a **receipt**; only then does the Adapter delete from the outbox.
5. The UI queries rollups through `api.py`; live counters are pushed at most twice per second.
Cost is computed at query time from `data/pricing.json`; only raw tokens are stored.

## Wire protocol (v1)
Transport: WebSocket over TLS 1.2+ on TCP 47800 (up to +20 on conflict). JSON frames:
* `hello` `{device_id, enroll_secret | device_key (exactly one), team, version}` - must arrive within 10 s; at most 4 KiB before authentication.
* `welcome` `{device_key}` - issued on first enrollment; later connects use the device key.
* `batch` `{events:[UsageEvent...]}` - fields strictly validated (token bounds, types, no unknown keys).
* `receipt` `{message_ids}` and `reject` `{reason}`.
Discovery: UDP port 47801. Query `{v, q:"who-is-tokenvault", manager_id, nonce}` broadcast by the Adapter; answer `{v, a:"tokenvault", manager_id, port, fingerprint, nonce}` (strict parsing, datagram at most 512 bytes, amplification factor under 2.5, per-source rate limit).

## Security model
* **Pinned self-signed certificate** (ECDSA P-256, 10 years, generated on first run). Installers carry its SHA-256 fingerprint; the Adapter checks the fingerprint, not host names or a CA. A discovery answer is accepted only with matching nonce, manager id and fingerprint, and TLS pinning is checked again afterwards.
* **Enrollment secret** per installer package + **per-device key** issued on first connect. Packages and devices can be revoked (live sessions closed with code 4003). The installer file therefore is a credential.
* **DPAPI** protects the Manager's private-key password and the Adapter's `device.key` / `enroll.secret` (fail closed). The private key is stored as encrypted PKCS#8.
* Server limits: connection caps per IP/total, handshake and hello timeouts, `SO_EXCLUSIVEADDRUSE` binding.
* **Privacy**: metadata only (see [privacy.md](privacy.md)); no pause/hide control - the tray icon and "What is sent?" view keep the Adapter transparent.
* Installer payload (TVLTCFG1 footer) is not signed; trust comes from the pinned fingerprint plus the enrollment secret. The macOS and portable-Windows packages carry the same payload in a sidecar file `tokenvault-config.bin` inside the ZIP (nothing is appended to a Mach-O or exe).
* Adapter installs per user without admin (`%LOCALAPPDATA%`, HKCU Run); only the Manager firewall step is elevated.
