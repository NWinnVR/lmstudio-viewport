← [README](../README.md) · [Wiki index](index.md)

# Changelog

Newest first. Tags match `VERSION` in [`dashboard.py`](../dashboard.py) and the GitHub releases.

## v1.3.4 — 2026-10-07
- **Favicon** added: multi-size `.ico` + 128px PNG (decode-pink ECG pulse + cyan "live" dot), served from `dashboard.py`'s static handler; two `<link rel="icon">` tags in the head.
- **Launchers hardened:** `start_viewport.bat` / `start_viewport_startup.bat` now prefer the Hermes 3.11 venv's `pythonw` over bare PATH, so a future Python/PATH change can't silently drop the `psutil` dependency (RAM tile would hide).
- **Watchdog fix (`0255a8e`):** `holder_live()` crashed on a Windows zombie lock-holder (invalid subprocess args + conservative "assume alive") → every new watchdog exited as a stray and the dashboard never respawned. The liveness probe is fixed; a dead holder's lock is now claimable again.

## v1.3.3 — 2026-10-04
- **Per-day rollup now includes no-LLM days** — the table used to key rows off the generation log only, so any day the viewport ran without an LLM task vanished from it (exactly the days a 24/7 logger is supposed to capture). Days are now taken as the **union** of the generation + lifetime logs; no-LLM days show real GPU W / kWh / cost with `—` for token stats.

## v1.3.2 — 2026-09-16
- **Live CPU package watts via LibreHardwareMonitor (optional).** A small LHM sensor server feeds real CPU watts over a local HTTP API; when it's up, *every* energy integral (session / per-day / lifetime) switches to live watts automatically — no extra config. Without LHM the `cpu_w` estimate is used as before.
- **New power model:** wall draw = `(GPU + CPU + platform_w) / psu_eff`, with a `PLATFORM_W` "rest of system" slice and PSU-efficiency correction, all editable in Settings (`cpu_w`, `platform_w`, `psu_eff`, `lhm_port`).
- New files: `cpu_sensors.py`, `start_cpu_sensors.bat`, `stop_cpu_sensors.bat`, `fetch_lhm.py` (+ unit tests).

## v1.3.1 — 2026-09-16
- **Fixed the all-zeros write token** (a flaky webview RNG had minted a guessable key): token generation/validation moved to the server (`secrets.token_hex(12)`), weak tokens are rejected and auto-healed at startup, plus an owner-bypass (loopback / this machine) so the PC can never be locked out of its own viewport. New "↻ regenerate token" button in the phone card.

## v1.3.0 — 2026-09-16
- **Optional phone / LAN mode:** opt-in bind to the LAN + a write token that gates only the three *write* buttons (read-only stays open to your home network, private-profile firewall rule only — never internet-facing). One-step enable in Settings reveals the LAN url + exact `netsh` command.

## v1.2.0 — 2026-09-16
- **The dashboard can no longer relaunch LM Studio.** Every `lms` call is gated behind a passive TCP probe to port `:1234`, so with the app closed there are *zero* `lms` invocations and the GUI stays down — you can leave the viewport open all day. New **three-state status pill** (🟥 OFFLINE / 🟧 RUNNING · no model / 🟩 READY·GENERATING) + a live model label that never goes stale.
- Same release line: one-click start/stop launchers shipped, startup launcher made portable, zombie-proof watchdog single-instance guard.

## v1.1.x — 2026-09-13
- **v1.1.1** — ships `backup_to_private.py` + `run_backup.bat` so anyone can wire up their own one-click private backup repo.
- **v1.1.0** — a friendlier, more portable dashboard:

**v1.1.0** — a friendlier, more portable dashboard.

### Auto-detects your hardware 🎛️
The **GPU util** tile now shows *your* card's real name (from `nvidia-smi`)
instead of assuming everyone runs the same GPU. A 4090, an A5000, an RTX 3060 —
whatever you have is what the tile displays. Same for system RAM, which has
always auto-detected your total.

### Fairer cost default 💡
The electricity rate now defaults to the **US national residential average
(~18.34¢/kWh, EIA mid-2026)** rather than a single author's rate. Set your real
rate in **Settings → rate** (it persists to `settings.json`) and every cost
number — all-time, per-day, session — recalculates live.

### Attribution restored ✒️
The footer carries **Made by [NWinn](https://github.com/NWinnVR)** again —
attribution for the author, with a link to the project.

### Docs & credits 📖
- README now credits **LM Studio**, notes the **Grafana-inspired** (not
  Grafana-code) layout, and cites the **EIA** rate source.
- All screenshots re-shot to match.

**No data leaves your machine.** Logs, ledger, settings, and archives stay in
the project folder and are git-ignored.

## v1.0.0 — 2026-09-13
First public release.

**LM Studio Telemetry Viewport v1.0.0** — first public release.

A live, read-only cockpit for your local LM Studio inference server: real-time
decode/prefill/draft needles, GPU/VRAM/temp/draw, system RAM, prompt progress,
lifetime charts, a per-day rollup with energy + cost + **$ saved**, a generation
feed, and live-tweakable settings — with a durable all-time ledger that survives
restarts.

**Highlights**
- Strictly read-only: no API token, no second model, `127.0.0.1` only
- Counters flush to 0 when idle; averages computed over active generation only
- Status pill: `GENERATING` while working, `READY` when idle
- Portable: Windows `.bat` + Unix `.sh`, stdlib-first (optional `psutil`)
- MIT licensed, PII-free, live data git-ignored by default

**Quick start**
```
git clone https://github.com/NWinnVR/lmstudio-viewport.git
cd lmstudio-viewport
pip install psutil          # optional (system RAM tile)
# Windows: run_viewport.bat   ·   Unix: ./run.sh
# open http://127.0.0.1:18022
```
