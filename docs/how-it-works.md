← [README](../README.md) · [Wiki index](index.md)

# How it works

LM Viewport never calls the inference API. It taps the **local, read-only**
streams LM Studio is already producing:

| Source | Gives us |
|---|---|
| `lms log stream --source model` | generation events (tok/s, TTFT, context, draft) |
| `lms log stream --source runtime` | llama.cpp timings (prefill t/s, prompt progress, decode ticks) |
| `lms server status` / `lms ps` | server up/down, loaded model, context, queue |
| `nvidia-smi` | GPU util / VRAM / temp / power draw |
| `psutil` *(optional)* | system RAM |
| **LibreHardwareMonitor** *(optional)* | **live CPU package watts** (see [the CPU power section in docs/setup-and-use.md](setup-and-use.md#cpu-power-optional)) |

Everything is local, argument-list subprocesses (no shell strings), and the
process is windowless so it sits quietly in the background.
It binds to **127.0.0.1 only** by default — nothing is exposed to your network
unless you turn on [the phone / LAN section in docs/setup-and-use.md](setup-and-use.md#phone--lan-optional) below.

**It can never relaunch the app.** Every `lms` call is gated behind a passive
TCP probe to LM Studio's port `:1234` — a socket connect can't start a process.
So with the app closed the dashboard makes **zero** `lms` invocations (which is
exactly what used to wake the LM Studio GUI) and simply reports `OFFLINE`.
Keep the viewport open all day for GPU/RAM/energy monitoring and it will never
pop the app back up.

### Privacy by design
- No API key, no cloud, no telemetry sent anywhere.
- All data (logs, ledger, settings, archives) stays in the project folder.
- The server listens on `127.0.0.1` only by default (opt-in LAN mode is
  private-profile + token-gated — never internet-facing).
- Everything the dashboard writes is git-ignored by default — clone the repo,
  run it, and your personal numbers never leave your machine.
