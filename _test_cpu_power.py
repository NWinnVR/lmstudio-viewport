#!/usr/bin/env python3
"""Unit-test dashboard.read_cpu_power() + _wall_watts() against a MOCK LHM
server on 127.0.0.1:8085 that returns the EXACT data.json shape LHM emits
(Type + RawValue + Value, and a same-named CPU Package *Temperature* sensor
as the disambiguation trap). No admin, no real CPU needed."""
import json
import threading
import time
import http.server
import socketserver
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# LHM data.json shape — note the CPU Package Power AND Temperature siblings.
MOCK_TREE = {
    "Text": "Computer",
    "Children": [
        {"Text": "AMD Ryzen 7 5700X3D", "Children": [
            {"Text": "Powers", "Children": [
                {"Text": "CPU Package", "Type": "Power", "Value": "52.3 W",
                 "RawValue": 52.3, "Min": 0.0, "Max": 142.0, "IsControl": False},
                {"Text": "CPU Core #1", "Type": "Power", "Value": "9.1 W",
                 "RawValue": 9.1, "Min": 0.0, "Max": 100.0, "IsControl": False},
            ]},
            {"Text": "Temperatures", "Children": [
                # THE TRAP: same name, different Type — a name-only matcher
                # would grab 61.7 as "watts".
                {"Text": "CPU Package", "Type": "Temperature", "Value": "61.7 °C",
                 "RawValue": 61.7, "Min": 0.0, "Max": 100.0, "IsControl": False},
                {"Text": "CPU Core #1", "Type": "Temperature", "Value": "70.2 °C",
                 "RawValue": 70.2, "Min": 0.0, "Max": 100.0, "IsControl": False},
            ]},
        ]},
    ],
}

# Variant with NO Type/RawValue fields (older-build shape Claude's script saw).
MOCK_LEGACY = {
    "Text": "Computer",
    "Children": [
        {"Text": "AMD Ryzen 7 5700X3D", "Children": [
            {"Text": "Powers", "Children": [
                {"Text": "CPU Package", "Value": "48.9 W"},
                {"Text": "CPU Core #1", "Value": "8.0 W"},
            ]},
            {"Text": "Temperatures", "Children": [
                {"Text": "CPU Package", "Value": "59.0 C"},
            ]},
        ]},
    ],
}

CUR = {"tree": MOCK_TREE}

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(CUR["tree"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

srv = socketserver.TCPServer(("127.0.0.1", 0), H)   # port 0 -> OS picks a free port
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()
time.sleep(0.3)
PORT = srv.server_address[1]

import dashboard  # noqa
dashboard.CFG["lhm_port"] = PORT   # point the reader at our mock, not 8085

ok = True
def check(name, got, want):
    global ok
    good = (got == want)
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + f"{name}: got={got!r} want={want!r}")

print("== modern LHM shape (Type + RawValue, incl. temp-sensor trap) ==")
check("read_cpu_power()", dashboard.read_cpu_power(), 52.3)   # must NOT be 61.7 (temp)
eff = dashboard.CFG.get("psu_eff", dashboard.PSU_EFF_PCT) / 100.0
plat = dashboard.CFG.get("platform_w", dashboard.PLATFORM_W)
check("_wall_watts(200 gpu, 52.3 cpu) live path",
      round(dashboard._wall_watts(200.0, 52.3), 2),
      round((200.0 + 52.3 + plat) / eff, 2))

print("== legacy shape (no Type/RawValue, Value string only) ==")
CUR["tree"] = MOCK_LEGACY
w = dashboard.read_cpu_power()
# Legacy: no Type field -> Type=="Power" fails -> RawValue missing -> Value parse.
# Our reader falls back to parsing the "Value" string. Expect 48.9, NOT 59.0.
check("read_cpu_power() legacy", w, 48.9)

print("== LHM down (connection refused) ==")
srv.shutdown()
time.sleep(0.2)
check("read_cpu_power() down -> None", dashboard.read_cpu_power(), None)

print("== _wall_watts with cpu=None (fallback) ==")
# One consistent formula: fallback CPU + platform, all / eff (no jump vs live).
fb = dashboard.CFG.get("cpu_w", dashboard.CPU_W_ESTIMATE)
check("_wall_watts(100, None) uses cpu_w fallback + platform / eff",
      round(dashboard._wall_watts(100.0, None), 2),
      round((100.0 + fb + plat) / eff, 2))

print()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
