← [README](../README.md) · [Wiki index](index.md)

# Troubleshooting

- **GPU/VRAM/temp tiles are blank** → `nvidia-smi` isn't on your `PATH` (no
  NVIDIA driver, or an AMD/Intel GPU). Those tiles simply stay empty; the rest works.
- **System RAM tile is missing** → `psutil` isn't installed. `pip install psutil`.
- **Decode/prefill read 0** → the model is idle right now (no active generation).
  They light up the moment the model starts producing tokens.
- **`lms` not found** → set `LMSTUDIO_BIN` to the full path of your `lms` CLI,
  or ensure LM Studio's `bin` is on your `PATH`.
- **Port already in use** → another viewport is running; or set `VIEWPORT_PORT=18023`.
- **CPU power line is empty / flat** → LibreHardwareMonitor isn't running
  (or isn't elevated). Double-click `start_cpu_sensors.bat`, then click
  **Options ▸ Remote Web Server ▸ Run** in the LHM window. The viewport
  falls back to the `cpu_w` estimate until LHM is up — it's not an error.
- **`start_cpu_sensors.bat` times out** → port `8085` is held by something
  else. Run `stop_cpu_sensors.bat` first, or change the port in LHM's
  Remote Web Server options + the viewport's `lhm_port` setting to match.
