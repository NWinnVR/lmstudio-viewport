#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
#  cpu_sensors.py — LibreHardwareMonitor setup / launch / verify helper
#
#  Purpose: make "real CPU package watts" available to the viewport with as
#  little friction as possible. LHM is a GUI app that (a) needs admin rights
#  to read the CPU power MSRs, and (b) starts its local sensor web server on
#  a manual "Run" click. This script handles the plumbing around that:
#
#    setup   Normalise LHM's user-settings file so the "Remote Web Server"
#            menu starts UNCHECKED (guarantees one clean click = server on)
#            and pins the port to 8085.
#    kill    Force-stop every running LibreHardwareMonitor.exe (clears stale /
#            non-elevated instances that may be squatting the sensor port).
#    launch  Start LibreHardwareMonitor.exe detached (survives this console).
#    wait    Poll http://localhost:8085/data.json until the sensor server is
#            up (waits for the user's one "Run" click), then report the
#            current CPU package watts as proof it's live.
#    check   One-shot: hit the server and print the CPU package watts now.
#    all     setup -> kill -> (wait for port) -> launch -> wait
#            (what the .bat runs; self-heals stale instances)
#
#  Run the self-elevating wrapper:  start_cpu_sensors.bat
#  (or, already in an admin shell):  python cpu_sensors.py all
# ─────────────────────────────────────────────────────────────────────────────
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
LHM_DIR = os.path.join(HERE, "vendor", "LibreHardwareMonitor")
LHM_EXE = os.path.join(LHM_DIR, "LibreHardwareMonitor.exe")
# LHM user-settings file = <exe path> with the extension swapped to .config
# (see PersistentSettings.Load in LHM's source). Distinct from the .NET
# runtime config (LibreHardwareMonitor.exe.config), which we must not touch.
LHM_SETTINGS = os.path.join(LHM_DIR, "LibreHardwareMonitor.config")
PORT = 8085
DATA_URL = "http://127.0.0.1:%d/data.json" % PORT

# Keys we manage in LHM's user-settings. We only ever WRITE these three; any
# other keys LHM itself saved (window position, theme, ...) are preserved.
MANAGED = {
    # Start with the server OFF (menu unchecked) -> a single "Run" click turns
    # it on. LHM's UserOption constructor reads this but does NOT auto-start,
    # so leaving it "true" would show a checked-but-dead menu and require two
    # clicks. "false" keeps the interaction to exactly one click, every time.
    "runWebServerMenuItem": "false",
    "listenerPort": str(PORT),
    "authenticationEnabled": "false",
}


def _log(msg):
    print(msg, flush=True)


def cmd_setup():
    """Write/patch LHM's user-settings XML. Tolerant of a missing file."""
    # Desired final appSettings: our three managed keys. Preserve any others.
    existing = {}
    if os.path.exists(LHM_SETTINGS):
        try:
            root = ET.parse(LHM_SETTINGS).getroot()
            for node in root.iter("appSettings"):
                for add in node.findall("add"):
                    k = add.get("key")
                    if k:
                        existing[k] = add.get("value")
        except Exception as e:
            _log("  (note: could not parse existing settings: %s)" % e)
    merged = dict(existing)
    merged.update(MANAGED)

    # Build the exact XML shape PersistentSettings.Save() writes (and .Load()
    # reads): configuration > appSettings > add[key, value].
    root = ET.Element("configuration")
    app = ET.SubElement(root, "appSettings")
    for k, v in merged.items():
        ET.SubElement(app, "add", {"key": k, "value": str(v)})
    tree = ET.ElementTree(root)
    try:
        tree.write(LHM_SETTINGS, encoding="utf-8", xml_declaration=True)
        _log("  settings normalised -> %s" % os.path.basename(LHM_SETTINGS))
    except Exception as e:
        _log("  FAILED to write settings: %s" % e)
        return False
    return True


def cmd_kill():
    """Force-stop every running LibreHardwareMonitor.exe.
    Safe to call when nothing is running (no-op)."""
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/IM", "LibreHardwareMonitor.exe"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            _log("  cleared existing LibreHardwareMonitor instance(s)")
        else:
            _log("  no existing LibreHardwareMonitor to clear (fine)")
        return True
    except Exception as e:
        _log("  (kill: %s)" % e)
        return True  # not fatal — launch will still try


def _port_free(port, timeout=8.0):
    """Wait until nothing is LISTENING on the given TCP port (or timeout)."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.5)
    return False


def cmd_launch():
    if not os.path.exists(LHM_EXE):
        _log("  LHM not found at:\n    %s" % LHM_EXE)
        _log("  Re-download it with:  python fetch_lhm.py")
        return False
    flags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — keeps LHM alive after
        # the launcher console closes; no console attached to it.
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([LHM_EXE], cwd=LHM_DIR, creationflags=flags)
        _log("  LibreHardwareMonitor launched (window should appear / tray icon)")
    except Exception as e:
        _log("  FAILED to launch LHM: %s" % e)
        return False
    return True


def _fetch_data(timeout=1.0):
    with urllib.request.urlopen(DATA_URL, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _find_power(node, name):
    """Mirror of dashboard._lhm_search_power — find Text==name & Type=='Power'."""
    if isinstance(node, dict) and node.get("Text") == name and node.get("Type") == "Power":
        rv = node.get("RawValue")
        if isinstance(rv, (int, float)) and rv == rv and rv >= 0:
            return float(rv)
        try:
            return float(str(node.get("Value", "")).strip().split()[0])
        except Exception:
            return None
    for ch in (node.get("Children") if isinstance(node, dict) else None) or []:
        w = _find_power(ch, name)
        if w is not None:
            return w
    return None


def cmd_check():
    try:
        tree = _fetch_data()
    except Exception as e:
        _log("  NOT REACHABLE (%s)" % e.__class__.__name__)
        return False
    for sensor in ("CPU Package", "CPU Core", "CPU"):
        w = _find_power(tree, sensor)
        if w is not None:
            _log("  OK: %s power = %.1f W   (%s)" % (sensor, w, DATA_URL))
            return True
    _log("  server up, but no CPU power sensor found -> LHM likely NOT elevated.")
    _log("  Re-run the launcher (it asks for admin) so it can read CPU power MSRs.")
    return False


def cmd_wait(max_seconds=150):
    _log("  waiting for the sensor server on %s ..." % DATA_URL)
    _log("  In the LibreHardwareMonitor window, click:   Options  >  Remote Web Server  >  Run")
    _log("")
    deadline = time.time() + max_seconds
    started = time.time()
    while time.time() < deadline:
        try:
            tree = _fetch_data()
        except Exception:
            time.sleep(1.5)
            continue
        # Server is up — report CPU power (proof the sensor path works).
        for sensor in ("CPU Package", "CPU Core", "CPU"):
            w = _find_power(tree, sensor)
            if w is not None:
                _log("  ✓ sensor server UP after %.0fs" % (time.time() - started))
                _log("    %s power = %.1f W" % (sensor, w))
                _log("")
                _log("  You can close this window — LHM keeps running in the tray.")
                _log("  The viewport now reads live CPU watts (orange line on the CPU graph).")
                return True
        # Server up but sensor missing (not elevated) — tell them, keep waiting.
        _log("  …server is up but the CPU power sensor is absent (LHM not elevated?).")
        time.sleep(2)
    _log("  timed out. If you clicked Run and this still fails, check that LHM is running as admin.")
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "setup":
        ok = cmd_setup()
    elif cmd == "kill":
        ok = cmd_kill()
    elif cmd == "launch":
        ok = cmd_launch()
    elif cmd == "check":
        ok = cmd_check()
    elif cmd == "wait":
        ok = cmd_wait()
    elif cmd == "all":
        ok = cmd_setup() and cmd_kill()
        # Give the killed instance(s) a moment to release the port before
        # we launch a fresh one.
        if not _port_free(PORT, timeout=10.0):
            _log("  (port %d still busy after kill — the new instance may fail to bind; try again)" % PORT)
        ok = cmd_launch() and ok
        ok = cmd_wait() and ok
    else:
        print("unknown command: %s (try: setup | kill | launch | wait | check | all)" % cmd)
        sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
