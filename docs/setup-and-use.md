← [README](../README.md) · [Wiki index](index.md)

# Setup & everyday use

## CPU power (optional)

The dashboard already meters **GPU** watts live (via `nvidia-smi`). For the
**CPU** side, Windows won't expose package power to a plain process — but
[LibreHardwareMonitor (LHM)](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
can, and it serves its sensors over a small local HTTP API. Point the viewport
at it and the CPU-power line (orange, on the CPU graph) fills with **real
watts** instead of the flat `cpu_w` fallback.

It's **optional**: without LHM the viewport runs exactly as before, using the
`cpu_w` estimate. With LHM, every energy integral (session, per-day, lifetime)
switches to live CPU watts automatically — no extra config.

### Setup (one-time, then it's hands-off)
1. **Download LHM** into the project (already done if you pulled a fresh repo
   that ships `vendor/`; otherwise):
   ```
   python fetch_lhm.py
   ```
2. **Start the sensor** — double-click `start_cpu_sensors.bat`. It:
   - asks for **admin** (LHM needs it to read the CPU power MSRs — without it,
     the CPU power sensor is simply absent),
   - clears any stale LHM instance,
   - launches LHM (it stays in the system tray),
   - waits for the sensor server, then **prints the live CPU watts** as proof.
3. **One click:** in the LHM window, click **Options ▸ Remote Web Server ▸ Run**.
   The menu starts unchecked on purpose, so this is always exactly one click.
   Close the console — LHM keeps running in the tray.

Done. The viewport now reads live CPU watts. To stop the sensor later,
double-click `stop_cpu_sensors.bat` (or exit LHM from its tray icon) — the
viewport transparently falls back to the `cpu_w` estimate.

**Port:** the sensor server listens on `8085` by default (change it in the
Settings panel `lhm_port` *and* in LHM's Remote Web Server options to match).
---

## Phone / LAN (optional)

Open the dashboard on your phone or any device on your home network. It's
**off by default** — the viewport stays `127.0.0.1`-only until you turn it on,
and it **never goes to the internet** (the firewall rule is private-profile only).

**How it's secured:** read-only stats are open to the LAN, but the three *write*
buttons (save settings · compact · close) are gated by a **write token**. Anyone
on the network can look at your telemetry; only a device that has the token can
press a button.

### Enable it (2 steps)
1. In the dashboard, open the **📱 phone / LAN** card (settings drawer) and click
   **enable phone mode**. This generates a token, binds the viewport to the LAN,
   and shows the LAN url + the firewall command.
2. Run the shown command **once, in an admin prompt** (it opens the port to your
   home network only):
   ```
   netsh advfirewall firewall add rule name="Viewport-18022" dir=in action=allow protocol=TCP localport=18022 remoteip=LocalSubnet profile=private
   ```
   Then restart the viewport (the watchdog relaunches it with the new bind).

### Open it on the phone
Click **copy url** on the card and paste it into your phone's browser — the url
carries the token (`…#token=…`), which the phone stores automatically on first
load, so the write buttons work there too. (The phone's own "📱 phone / LAN"
card also shows the token if you ever need to re-enter it.)

### Disable it
Click **disable** on the card — it clears the token and re-binds to `127.0.0.1`
(restart to apply). To remove the firewall rule later:
```
netsh advfirewall firewall delete rule name="Viewport-18022"
```
---

## Configuration

| Knob | Default | Meaning |
|---|---|---|
| `cost_per_kwh` | `0.1834` | your electricity rate, $/kWh — **defaults to the US national residential average** (EIA, ~18.34¢ as of mid-2026); set it to your real rate in the Settings panel |
| `cpu_w` | `65.0` | **CPU fallback** — used only when LibreHardwareMonitor isn't running (no live CPU watts). With LHM up, real CPU package watts are read live and this is ignored |
| `platform_w` | `25.0` | "rest of system" draw (board, RAM, SSDs, HDDs, fans) — the non-CPU, non-GPU slice of the wall meter |
| `psu_eff` | `90.0` | PSU efficiency %, applied to the whole system so component watts map to wall watts (80+ Platinum ≈ 90 at part load) |
| `lhm_port` | `8085` | LibreHardwareMonitor sensor web-server port |
| `frontier_in` | `3.2` | avg frontier API input price, $/M tokens (for `$ saved`) |
| `frontier_out` | `13.8` | avg frontier API output price, $/M tokens |
| `idle_power_w` | `110.0` | GPU draw below this = "not working" (gates `$ saved` + active time) |
| `lifetime_days` | `30.0` | days of lifetime samples kept live before compacting to `archive/` |

> **Power model.** The viewport meters the **wall** draw as
> `(GPU_w + CPU_w + platform_w) / (psu_eff / 100)`. GPU watts come from
> `nvidia-smi`; CPU watts come from LibreHardwareMonitor when it's running
> (else the `cpu_w` fallback); `platform_w` is the everything-else slice.
> All three are editable in the Settings panel.

Set them in the **Settings** panel, or create a `settings.json`
(see [`settings.example.json`](../settings.example.json)). Environment overrides:
`VIEWPORT_PORT` (default `18022`), `LMSTUDIO_BIN` (explicit path to the `lms` CLI).

## Files — what's in this repo

```
dashboard.py            the whole server + API + log parsers (stdlib + optional psutil)
index.html              the dashboard UI (self-contained, no build step)
run_viewport.bat        Windows double-click launcher
run.sh                  Linux / macOS launcher
settings.example.json   template for your settings.json
requirements.txt        (optional) psutil
docs/                   the wiki + README screenshots

# CPU power (optional — live CPU package watts)
cpu_sensors.py          LHM setup/launch/verify helper (stdlib)
start_cpu_sensors.bat   double-click → UAC → launches LHM + waits for the sensor
stop_cpu_sensors.bat    double-click → force-kills LHM
fetch_lhm.py            reproducible downloader for LibreHardwareMonitor (v0.9.6)
_test_cpu_power.py      unit tests for the CPU-power reader + wall-watts math
vendor/LibreHardwareMonitor/   the LHM binary (git-ignored; re-fetch via fetch_lhm.py)
```
