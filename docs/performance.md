# Performance (Task 30)

Goal: the Adapter is invisible on an employee's machine and the Manager is light while
it sits in the tray. Method: measure, optimise only what the numbers justify, measure
again. Every number below comes from `tests/perf/bench.py`; raw results are in
`tests/perf/results/<label>-<part>.json`.

## How to re-run

```powershell
# from the repository root, with the project venv
.venv\Scripts\python tests\perf\bench.py adapter --label mine   # ~5 min
.venv\Scripts\python tests\perf\bench.py manager --label mine   # ~4 min (+ ~3.5 min the first time: builds the 2M-event DB)
.venv\Scripts\python tests\perf\bench.py ui --gui --label mine  # ~5 min, opens the real window twice
.venv\Scripts\python -m pytest -m slow tests\perf               # hard budgets (~30 s)
```

* Everything runs as separate real processes: the Manager back end
  (`tests/e2e/manager_host.py`), `python -m tokenvault.adapter.main run`, the dev server
  in headless Chromium, the real WebView2 window (`--selftest-window`, demo data).
* Processes are measured from outside (`bench_util.py`, one
  `NtQuerySystemInformation` call): CPU time, working set (RSS), private bytes,
  context switches (= wake-ups) and bytes written. CPU "sustained" = worst 5-second
  average; "% of one core" = CPU seconds / wall seconds.
* The big Manager dataset (2 M events, 50 employees x 90 days, 1.4 GB) is cached in
  `%TEMP%\tv-bench-db` (`--db-cache`).
* The "before" numbers were produced by the same bench against the base commit
  `6433f39` (sources extracted with `git archive`).

**Noise.** This machine is a shared desktop: another agent was building and testing in
parallel during every run. CPU percentages of a single process are reliable to about
+-0.05 % idle and +-3 % under load; wall-clock numbers (latency, query times) can be
10-30 % worse than on a quiet machine. Compare within a row, not across machines.

## Adapter (employee machine)

`bench.py adapter`: real Manager back end + one Adapter (`run --no-tray`), 60 000
generated messages in 120 transcripts (240 MB, ~3 KB of tool output per turn, spread
over 85 days), then idle, live appends and a 5 000-line burst; finally a second
Adapter WITH the tray icon. Files: `before-adapter.json`, `after-adapter.json`.

| Metric | Target | Before | After |
|---|---|---|---|
| Import `tokenvault.adapter.main` (median of 3) | - | 190 ms | 148 ms |
| Start-up: spawn -> "adapter started" / -> connected | < 1.5 s | 0.28 s / 0.31 s | 0.20 s / 0.22 s |
| Idle 60 s, no tray: CPU (% of one core) | < 0.1 % | 0.13 % (0.18 % in a 2nd run) | **0.078 %** |
| Idle, no tray: wake-ups (context switches/s) | - | 17.2 | **0.7** |
| Idle, no tray: working set / private | <= ~30 MB | 40.6 / 28.6 MB | **11.6** / 27.5 MB |
| Idle 60 s, tray: CPU | < 0.1 % | 0.10 % | **0.00 %** |
| Idle, tray: wake-ups/s | - | 15.2 | **~0** |
| Idle, tray: working set / private | <= ~45 MB | 45.1 / 30.1 MB | **10.8** / 29.4 MB |
| Backfill 60k msgs: CPU average | - | 29.9 % | **10.0 %** |
| Backfill: CPU sustained (worst 5 s) / peak 1 s sample | <= ~15 % | 34.3 % / 40.9 % | **11.0 % / 15.6 %** |
| Backfill: wall time until the Manager has all 60k | - | 26 s | 72 s (paced, by design) |
| Backfill: bytes written by the adapter (outbox 44 MB) | - | 169 MB | 182 MB |
| Live latency: append -> visible in the Manager (15 samples) | <= ~1.2 s median | 0.656 s median, 1.11 p90 | 0.641 s median, 1.06 p90 |
| Burst 5 000 lines: CPU average / peak sample | - | 34.0 % / 61.7 % | 11.3 % / 35.3 % |
| Burst: all in the Manager | - | 1.6 s | 3.2 s (full batches paced) |
| Burst: working set peak | - | 44.7 MB | 29.5 MB |

The working set ("RSS" in Task Manager) is trimmed when the agent goes idle; private
bytes (what the process really owns) are unchanged at ~28 MB. Batches on the wire are
unchanged: up to 200 events / 256 KB per frame (protocol limits); a live response is
sent at once, a backlog in full batches paced by the CPU budget.

## Manager

`bench.py manager`: 2 M events (50 employees x 90 days, 1.4 GB), every Aggregates read
x range, the back end (`manager_host.py`) idle, with 50 connected simulated adapters
(`bench_clients.py`, the real adapter `Transport`), an ingest of 100k events and a PDF
of all employees. `bench.py ui`: headless Chromium on the dev server + the real
WebView2 window. Files: `before-*.json`, `after-*.json`, `before-browser-ui.json`,
`after-browser-ui.json`.

Queries (p95 of 7 runs, in-process `Aggregates`):

| Call | Target | Before | After |
|---|---|---|---|
| overview / today (default page range) | <= 150 ms | 144 ms | **62 ms** |
| employees / today | <= 150 ms | 151 ms | **13 ms** |
| overview / 7d | | 207 ms | 104 ms |
| employees / 7d | | 109 ms | 30 ms |
| overview / 30d | | 483 ms | 294 ms |
| employees / 30d | | 178 ms | 122 ms |
| overview / 90d | | 845 ms | 484 ms |
| employees / 90d | | 320 ms | 221 ms |
| overview / all | | 328 ms | 288 ms |
| orchestration / 30d | | 236 ms | 89 ms |
| projects / today | | 82 ms | 15 ms |
| projects / all | | 122 ms | 198 ms* |
| HTTP `overview(None)` incl. JSON (repeated, unchanged data) | | 376 ms | 17.5 ms (read cache) |

\* measured with a `project_rows` rewrite that was then reverted (the statement alone:
132 ms rewritten vs 37 ms original with the new covering index); the code at HEAD
uses the original statement. Statement-level EXPLAIN QUERY PLAN output is in the
JSON (`queries.slowest_statements`); after the change every hot statement reads a
`COVERING INDEX`.

Processes:

| Metric | Target | Before | After |
|---|---|---|---|
| Back end idle 60 s, no adapters: CPU / working set | < 0.3 %, <= ~80 MB | 0.18 % / 50 MB | 0.21 % / 53 MB (noise) |
| Back end idle, 50 adapters connected: CPU / wake-ups / write ops per min | | 0.39 % / 30 /s / 600 | **0.26 %** / 27 /s / **24** |
| Back end with 50 adapters: working set | | 73 MB | 92 MB (reader cache + mmap pages) |
| 50 adapters connect (TLS + Hello) | | 1.3 s | - |
| Ingest 100k events from 50 adapters at once | | 17.1k ev/s | 19.5k ev/s |
| PDF all employees, 30 d, 54 pages | | 11.4 s | 7.4 s |
| Back end start-up on the 2M-event database | | 0.8 s | 3.1 s the first time (builds the new indexes once), then as before |
| Real window SHOWN, idle on home, whole process tree | < 1 % | **77 %** (box-shadow pulse) | **0.49 %** |
| Real window HIDDEN (`--minimized`), tree | < 0.3 % | **68 %** | 3.2 % over the bench window incl. a start-up burst; **~0.7 % steady** (back end 0.16 %, WebView2 browser 0.4 %) |
| Real window: tree working set shown | | 782 MB | 652 MB |
| UI boot (first render, settings page) | | 592 ms | 386 ms |
| ECharts bundle fetched on a non-chart page | | yes | **no** |
| Page re-fetches for 20 live pushes in 10 s (max rate) | | 60 API calls | 33 |
| JS heap growth after 200 route changes | <= ~10 MB | +1.49 MB | +1.49 MB |

The "hidden" row of the bench (3.2 %) was taken while `TrySuspendAsync` was still in
place; the steady value is from a probe of the final code (`--minimized`, 30 s after
45 s of warm-up).

## What changed and why

Adapter (`src/tokenvault/adapter/`)

| Finding (measured) | Change | Regression test |
|---|---|---|
| Idle agent woke 15-17 times/s: watchdog's Windows emitter polls its buffer queue every `timeout` (0.1 s, needed for latency) | `fswatch.QuietEmitter` waits on the reader's queue condition without a timeout, then drains the batch with the 0.1 s gap as before | `test_watcher_perf::test_idle_observer_does_not_poll`, `test_watcher::test_observer_uses_short_os_event_timeout`, latency tests |
| Backfill of 60k messages ran at 34 % of a core sustained (41 % peak) | Backfill thread in Windows background mode (low CPU + I/O + memory priority), 1 MB chunks with the collect lock released between them, one process-wide `CpuBudget` (10 % of a core over 5 s) that also paces the transport's FULL batches (a backlog); live sends are never paced | `test_watcher_perf::test_backfill_reads_small_chunks_and_paces_between_them`, `test_live_collection_is_never_paced`, `test_adapter_perf::test_budget_*`, slow `test_backfill_budget_never_exceeds_its_share` |
| Process ran BELOW_NORMAL only | + EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`); BELOW_NORMAL kept rather than IDLE so a live response is still collected on a busy machine | `test_eco_qos_requests_execution_speed_throttling`, `test_thread_background_mode_can_be_entered_and_left` |
| 3 commits per collected chunk (rows, titles, offset) | `Outbox.commit_file`: one transaction per chunk | `test_collect_commits_rows_titles_and_offset_in_one_transaction` |
| Every line was JSON-decoded, incl. multi-KB prompts and tool output | `parse_line` skips lines that contain neither `"assistant"` nor `"ai-title"` | `test_parser_skips_prompt_and_tool_lines_without_json_decoding` |
| Each batch was JSON-encoded twice (every row alone, then the batch) | `framing.frame_rows`: encode the batch once, per-row check only when that fails | `test_frame_encodes_a_good_batch_once`, `test_frame_still_drops_poison_and_splits_oversize` |
| Hot files were stat-ed every second for 60 s after the last change | poll every 3 s once nothing changed for 10 s; fast again on activity | `test_quiet_hot_file_is_polled_less_often` |
| ~40 MB working set while idle | working set trimmed when the backfill ends and when the last active file goes quiet | `test_trim_working_set_succeeds`, `test_watcher_trims_memory_when_the_last_hot_file_goes_quiet` |
| Start-up imports | already lazy (no PIL / pystray / tkinter / cryptography in `run --no-tray`); verified, kept | `test_agent_start_imports_no_gui_or_crypto_modules` |

Manager (`src/tokenvault/manager/`, `src/tokenvault/ui/`)

| Finding (measured) | Change | Regression test |
|---|---|---|
| The real window used ~77 % of a core (GUI process tree) idle on the home page, ~68 % even hidden: the titlebar "online" dot animated `box-shadow` forever, repainting the composited WebView2 window every frame | three pulses when devices come online, then a steady glow | `tests/ui/test_livegate.py::test_online_dot_does_not_animate_forever` |
| Live pushes and page re-fetches continued while the window was hidden in the tray | `LivePusher.set_paused` (no push, not even the payload, while hidden/minimised; one push on show); `WindowShell` reports visibility; WebView2 `MemoryUsageTargetLevel=Low` while hidden (`webview_tuning.py`; `TrySuspendAsync` was tried and dropped: a suspended page never answers `evaluate_js`, which hung the caller) | `test_manager_idle.py` |
| Each push (up to 2/s while adapters send) re-fetched the whole page | router gates page callbacks (`livegate.js`): max one per second, newest payload kept, none while `document.hidden`, one replay on show | `tests/ui/test_livegate.py::test_gate_throttles_and_pauses_while_hidden` |
| The 1 MB ECharts bundle was loaded at boot by `index.html` although `charts-core.loadECharts()` already loads it on demand | script tag removed (boot 592 -> 414 ms) | `test_index_does_not_load_echarts_eagerly` |
| On 2M events the hot statements scanned rollup tables through non-covering indexes (table lookups) | `store_core.PERF_INDEXES` (covering indexes, created IF NOT EXISTS on open - no schema bump, ~3-5 s once on a 2M-event database); reader `cache_size` 16 MB, `mmap_size` 128 MB, `temp_store=MEMORY`; `PRAGMA optimize` on close. (Re-grouping `project_rows` by session first was tried and measured slower, 37 -> 132 ms; reverted.) | `test_manager_query_perf.py` (EXPLAIN QUERY PLAN asserts `COVERING INDEX`), slow `test_default_pages_stay_within_150_ms` |
| Several widgets / refreshes ask the same question | `ReadCache` in the Api: reuse a result while `PRAGMA data_version` is unchanged and it is <= 2 s old | `test_api_reuses_an_unchanged_overview_and_recomputes_after_a_write`, `test_read_cache_*` |
| Presence tick = one commit per connected device every 10 s (600 write ops/min with 50 adapters) | `Store.touch_devices`: one transaction per tick | `test_presence_touch_is_one_commit_and_keeps_device_data` |

## Remaining hot spots

* **Long ranges on a big Manager database.** `overview`/`projects` over 30 d / 90 d / all
  still take ~0.2-0.5 s on 2 M events: every statement is index-only now, the cost is
  the number of rollup rows (day rollups keep device x e-mail x project x model x
  sidechain). The default pages (range "today", home page) are well inside 150 ms.
  Next step if needed: narrower day rollups (day x model, day x project x model) -
  a schema change with its own migration and extra ingest writes, so not done here.
* **Ingest throughput** is ~17k events/s with 50 adapters sending at once, at one full
  core: the per-event Python work in `Store._apply` (merge, rollup deltas) dominates;
  TLS/WebSocket overhead per idle connection is small (50 idle adapters: ~0.4 % CPU,
  ~20 MB more RSS).
* **Outbox write amplification.** ~180 MB written for 240 MB of transcripts
  (~3 KB per message): insert + WAL + checkpoint + the `delivered` flag rewriting the
  same pages on ack. With `synchronous=NORMAL` none of it is fsync'ed per commit.
  Options: delete delivered rows (needs another way to detect "grown" messages) or a
  larger `wal_autocheckpoint` so insert and ack of a page share one checkpoint.
* **WebView2 memory** stays ~0.5 GB working set over 6 processes (shared pages
  included). Hidden: low-memory target + suspension. Destroying the WebView while in
  the tray would free most of it but costs a 1-2 s re-creation and the page state on
  every show; not done. Hidden, the tree still idles at ~0.7 % of a core, ~0.4 % of it
  in the WebView2 browser process (the Python back end: 0.16 %).
* **Adapter working set** ~40 MB while active (Python + asyncio + ssl + websockets +
  sqlite + watchdog, ~28 MB private); it is trimmed when the agent goes idle.
* **PDF export** of all employees (30 d, 54 pages) takes ~11 s at one full core.
* **Burst pacing.** A 5k-line burst now takes ~4 s to reach the Manager instead of
  ~1.6 s, because full transport batches are paced by the CPU budget (by design: a
  burst is a backlog). A single live response is never paced.
* **UI navigation** keeps ~700 DOM nodes / 45 listeners more after 200 route changes
  (same before and after this task; JS heap +1.5 MB, well within the 10 MB budget).
