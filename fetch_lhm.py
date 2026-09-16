#!/usr/bin/env python3
"""Re-download and extract LibreHardwareMonitor into vendor/LibreHardwareMonitor.

Use if the vendored copy is ever missing, or to upgrade to a newer release.
Pure stdlib (urllib + zipfile). Default: latest release's classic (Windows) build.

    python fetch_lhm.py                 # latest release
    python fetch_lhm.py v0.9.6          # a specific tag
"""
import os
import sys
import json
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "vendor", "LibreHardwareMonitor")
API = "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor"


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "viewport-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_zip_url(tag):
    if not tag:
        rel = json.loads(_get(API + "/releases/latest"))
        tag = rel["tag_name"]
    assets = json.loads(_get(API + "/releases/tags/" + tag))
    # Prefer a "classic" Windows zip (non-ARM64, non-mac). The classic asset
    # is usually named like ...-classic-x64.zip or a bare .zip for windows.
    cands = [a["browser_download_url"] for a in assets.get("assets", [])
             if a["name"].lower().endswith(".zip")]
    # Rank: want the CLASSIC build (bare "LibreHardwareMonitor.zip" — .NET
    # Framework 4.x, already on Windows 10). Avoid the ".NET.<ver>" builds
    # (need that exact .NET runtime installed) and any ARM/Mac variants.
    def score(u):
        n = os.path.basename(u).lower()
        s = 0
        if "arm" in n or "aarch" in n:
            s -= 100
        if ".net." in n:          # .NET 10 / modern runtime builds -> needs install
            s -= 50
        if "mac" in n or "osx" in n:
            s -= 100
        if "librehardwaremonitor.zip" in n:   # the classic, bare-named build
            s += 40
        return s
    cands.sort(key=score, reverse=True)
    if not cands:
        raise SystemExit("No .zip asset found for tag %s" % tag)
    return cands[0], tag


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    url, tag = find_zip_url(tag)
    print("Downloading %s (%s)..." % (url, tag))
    buf = _get(url, timeout=300)
    zpath = os.path.join(HERE, "_lhm_download.zip")
    with open(zpath, "wb") as f:
        f.write(buf)
    print("Extracting to %s" % DEST)
    os.makedirs(DEST, exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(DEST)
    os.remove(zpath)
    exe = os.path.join(DEST, "LibreHardwareMonitor.exe")
    if not os.path.exists(exe):
        raise SystemExit("Extracted but LibreHardwareMonitor.exe not found at top level — "
                         "check the zip layout; you may need to move files up one level.")
    print("OK: LHM is at\n  %s" % exe)
    print("Now run:  start_cpu_sensors.bat")


if __name__ == "__main__":
    main()
