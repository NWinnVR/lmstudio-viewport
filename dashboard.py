#!/usr/bin/env python3
"""LM Studio Telemetry Viewport — read-only dashboard for the local LM Studio
inference server. Stdlib (+ psutil for system RAM). Zero model loads.

  Data sources (all read-only, no API token, no second model):
    lms log stream --json --source model      generation events (tok/s, TTFT, ctx, draft)
    lms log stream --json --source runtime    llama.cpp timings (prefill t/s, n_gen progress)
    lms server status --json                  server up/down + port
    lms ps --json                             loaded model, queue, ctx, lastUsedTime
    nvidia-smi                                GPU util / VRAM / temp / power
    psutil.virtual_memory()                   system RAM

  HTTP (127.0.0.1, :18022):
    GET  /                          viewport (index.html)
    GET  /api/status                server+model+GPU+RAM+prefill+progress+uptime+last_event
    GET  /api/events?n=             recent generation events
    GET  /api/series?metric=&range= time-bucketed series (1m/5m/15m/1h/1d/7d/1mo/all)
    GET  /api/lifetime?days=        lifetime rollup (gpu, vram, ram, power, cpu)
    GET  /api/history?days=7        per-day rollup (stats + energy + $ + active time)
    GET  /api/totals                all-time tokens + kWh/$ + active time + $ saved
    GET  /api/settings              live-tweakable knobs (rate, cpu_w, frontier, idle, retention)
    GET  /api/data                  live file sizes + archive size + retention
    POST /api/settings              update a knob (persisted to settings.json)
    POST /api/compact               rotate old samples to archive/ (ledger stays correct)
    POST /api/exit                  close the viewport (model/server untouched)

Long-term tracking: history.jsonl (per generation) + lifetime.jsonl (30s samples),
both persisted in this folder, so the graphs survive restarts and accumulate.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import psutil
except Exception:
    psutil = None

# prime the non-blocking CPU% baseline so the first real reading is valid
# (psutil.cpu_percent(None) returns % since the LAST call; first call = 0.0)
if psutil is not None:
    try:
        psutil.cpu_percent(None)
    except Exception:
        pass

PORT = int(os.environ.get("VIEWPORT_PORT", "18022"))
# Single source of truth for the release version (semver). Bump here; it is
# surfaced via /api/status and in the README. Policy: minor bump for fixes/adds,
# major only on explicit intent.
VERSION = "1.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "history.jsonl")
LIFETIME = os.path.join(HERE, "lifetime.jsonl")
LOGFILE = os.path.join(HERE, "viewport.log")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PROCESS_START = time.time()

# ── energy / cost knobs ──────────────────────────────────────────────────────
# Your electricity rate and a conservative CPU/platform wattage estimate.
# nvidia-smi reports GPU draw only; Windows can't reliably report CPU watts,
# so we fold a flat platform estimate into the kWh integral.
#
# These are LIVE-TWEAKABLE: they persist to settings.json and are editable from
# the dashboard's settings panel. `CFG` is the single source of truth at runtime.
COST_PER_KWH   = 0.1834  # $/kWh — US national residential average (EIA, Sep 2026). Set yours in the settings panel.
CPU_W_ESTIMATE = 65.0    # W — flat CPU+platform estimate (not meterable on Windows)
FRONTIER_IN    = 3.2     # $/M input tokens (avg frontier workhorse)
FRONTIER_OUT   = 13.8    # $/M output tokens
IDLE_POWER_W   = 110.0   # W — GPU draw below this = "not working" (gates $ Saved + active time)
JS_PER_KWH = 3_600_000   # W·s = Joules, NOT Wh — /1000 gives a 3600× over-read (bit us once)

CFG = {
    "cost_per_kwh":  COST_PER_KWH,
    "cpu_w":         CPU_W_ESTIMATE,
    "frontier_in":   FRONTIER_IN,
    "frontier_out":  FRONTIER_OUT,
    "idle_power_w":  IDLE_POWER_W,
    "lifetime_days": 30.0,   # keep lifetime+history samples this many days live; older → archive/
}

def _load_settings():
    try:
        with open(os.path.join(HERE, "settings.json"), encoding="utf-8") as f:
            d = json.loads(f.read())
    except Exception:
        return
    for k in list(CFG):
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            CFG[k] = float(v)

def _save_settings():
    try:
        with open(os.path.join(HERE, "settings.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(CFG, indent=2) + "\n")
    except Exception:
        pass

def get_settings():
    return dict(CFG)

def set_settings(d):
    for k, v in (d or {}).items():
        if k in CFG and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            CFG[k] = float(v)
    _save_settings()
    return dict(CFG)

# Where to find the `lms` CLI. Order: PATH, then the standard per-OS LM Studio
# locations, then a $LMSTUDIO_BIN override. No user-specific paths — portable.
def _lms_candidates():
    cands = [shutil.which("lms"),
             os.environ.get("LMSTUDIO_BIN")]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cands.append(os.path.join(local, "LM Studio", "bin", "lms.exe"))
        home = os.path.expanduser("~")
        cands.append(os.path.join(home, ".lmstudio", "bin", "lms.exe"))
    else:
        cands += ["/Applications/LM Studio.app/Contents/Resources/bin/lms",
                  os.path.expanduser("~/.lmstudio/bin/lms")]
    return [c for c in cands if c and os.path.exists(c)]

LMS = next(iter(_lms_candidates()), None)

SERVER = {"server": None, "model": None, "gpu": None, "ram": None,
          "prefill_tps": None, "prompt_progress": None, "asof": None,
          "uptime_sec": None, "last_event": None}
EVENTS = deque(maxlen=500)
LOCK = threading.Lock()
_session = {"gens": 0, "sum_tps": 0.0, "peak_tps": 0.0, "sum_ttft": 0.0,
            "n_ttft": 0, "sum_prefill": 0.0, "n_prefill": 0, "peak_prefill": 0.0,
            "sum_pred": 0, "sum_acc": 0, "sum_draft": 0, "since": time.time()}
_dying = False
_SERVER = None
_STREAM_LOCK = threading.Lock()      # guards _STREAMS
_STREAMS = {}                        # name -> live lms log-stream Popen (killed on exit)
# runtime stream: prefill + generation-progress (rolling for sparklines)
RUNTIME_TPS = deque(maxlen=10800)     # (ts, t/s) decode ticks — 3h window
RUNTIME_PROGRESS = deque(maxlen=10800)  # (ts, 0..1) live prompt-progress
RUNTIME_PREFILL = deque(maxlen=10800)  # (ts, prompt_tps)
RUNTIME_DRAFT = deque(maxlen=10800)    # (ts, draft-acceptance %)
RUNTIME_PRED = deque(maxlen=10800)     # (ts, n_gen) live tokens generated this turn
RUNTIME_PROGRESS_TASK = {}            # task_id -> last progress (for event attach)
RUNTIME_PREFILL_TASK = {}             # task_id -> peak prefill t/s seen for that task
_LAST_LIVE = {"decode": None, "pred": None, "draft_pct": None, "acc": None}
_PENDING_PROGRESS = deque(maxlen=20)  # (ts, max_progress, peak_prefill) waiting for the model event


# --------------------------------------------------------------------- util
def log(msg):
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def run_json(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        return json.loads(out) if out else None
    except Exception:
        return None


def server_status():
    return run_json([LMS, "server", "status", "--json"]) if LMS else None


def model_info():
    if not LMS:
        return None
    ps = run_json([LMS, "ps", "--json"], timeout=20)
    if isinstance(ps, list) and ps:
        return ps[0]
    return ps if isinstance(ps, dict) else None


def gpu_stats():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run([exe,
            "--query-gpu=utilization.gpu,memory.used,memory.total,"
            "temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"], capture_output=True, timeout=10,
           creationflags=NO_WINDOW)
        parts = [x.strip() for x in (p.stdout or b"").decode().strip().split(",")]
        if len(parts) < 5:
            return None
        out = {"util_pct": int(parts[0]), "vram_used_mb": int(parts[1]),
               "vram_total_mb": int(parts[2]), "temp_c": int(parts[3]),
               "power_w": float(parts[4])}
    except Exception:
        return None
    # Auto-detect the GPU's NAME (portable — never assume a specific card).
    try:
        np = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, timeout=10, creationflags=NO_WINDOW)
        nm = (np.stdout or b"").decode().strip().splitlines()
        if nm and nm[0].strip():
            out["name"] = nm[0].strip()
    except Exception:
        pass
    return out


def ram_stats():
    if psutil is None:
        return None
    try:
        m = psutil.virtual_memory()
        return {"used_mb": round(m.used / 1024 / 1024),
                "total_mb": round(m.total / 1024 / 1024),
                "pct": round(m.percent, 1),
                "avail_mb": round(m.available / 1024 / 1024)}
    except Exception:
        return None


# -------------------------------------------------------------- event stream
def make_event(o, d):
    s = d.get("stats") or {}
    ts = o.get("timestamp")
    def g(*names):
        for n in names:
            v = s.get(n)
            if v is not None:
                return v
        return None
    return {"ts": (ts / 1000.0) if isinstance(ts, (int, float)) else time.time(),
            "model": d.get("modelIdentifier") or d.get("modelPath"),
            "tps": g("tokensPerSecond"), "ttft": g("timeToFirstTokenSec"),
            "prompt": g("promptTokensCount", "promptTokens", "promptTokensCount"),
            "pred": g("predictedTokensCount", "predictedTokens"),
            "total": g("totalTokensCount", "totalTokens"),
            "draft": g("totalDraftTokensCount", "totalDraftTokens"),
            "acc": g("acceptedDraftTokensCount", "acceptedDraftTokens"),
            "rej": g("rejectedDraftTokensCount", "rejectedDraftTokens"),
            "stop": g("stopReason")}


def record_event(ev):
    # attach the max progress + peak prefill captured for this task (from the
    # runtime stream, keyed by task id) if it arrived before the model event.
    with LOCK:
        # find the most recent pending progress matching this event's timestamp
        for (pt, pv, pp) in reversed(list(_PENDING_PROGRESS)):
            if ev.get("ts") and abs(ev["ts"] - pt) < 5.0:
                ev["max_progress"] = round(pv, 3)
                if pp and pp > 0:
                    ev["prefill_tps"] = round(pp, 2)
                _PENDING_PROGRESS.remove((pt, pv, pp))
                break
        EVENTS.appendleft(ev)
        if ev.get("tps"):
            _session["gens"] += 1
            _session["sum_tps"] += ev["tps"]
            _session["peak_tps"] = max(_session["peak_tps"], ev["tps"])
        if ev.get("ttft"):
            _session["sum_ttft"] += ev["ttft"]
            _session["n_ttft"] += 1
        _session["sum_pred"] += ev.get("pred") or 0
        _session["sum_acc"] += ev.get("acc") or 0
        _session["sum_draft"] += ev.get("draft") or 0
        SERVER["last_event"] = ev
    try:
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
    except Exception as e:
        log("history write failed: %s" % e)
    # keep the durable all-time ledger current (survives archival compaction)
    try:
        _ledger_add_event(ev)
    except Exception as e:
        log("ledger event update failed: %s" % e)


# ------------------------------------------------------------- runtime parse
_RE_TGS = re.compile(r"n_gen\s*=\s*(\d+),\s*tg\s*=\s*([\d.]+)\s*t/s")
_RE_PROMPT_SUMMARY = re.compile(
    r"prompt eval time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*tokens\s*\(.*?([\d.]+)\s*tokens per second\)")
_RE_TASK = re.compile(r"task\s+(\d+)")
# LIVE prompt-processing lines — the real source of LM Studio's progress bar +
# prefill t/s (your 800-900). Format:
#   ... task 57647 | prompt processing, n_tokens = 14336, progress = 0.21, t = 15.95 s / 898.87 tokens per second
_RE_PROMPT_LIVE = re.compile(
    r"task\s+(\d+)\s*\|\s*prompt processing,\s*n_tokens\s*=\s*(\d+),\s*progress\s*=\s*([\d.]+),\s*t\s*=\s*[\d.]+\s*s\s*/\s*([\d.]+)\s*tokens per second")
# end-of-generation summary — the one that closes out a task
_RE_END_SUMMARY = re.compile(r"total time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*tokens")
# LIVE draft-acceptance (mid-generation):
#   "... draft acceptance = 0.82653 (   81 accepted /    98 generated), mean len =  2.62"
_RE_DRAFT = re.compile(r"draft acceptance\s*=\s*([\d.]+)\s*\(\s*(\d+)\s*accepted\s*/\s*(\d+)\s*generated\)")


def parse_runtime_line(o, d):
    """Extract prefill t/s, live prompt progress, live decode t/s + pred count,
    and live draft-acceptance from llama.cpp print_timing lines.

    The live `n_gen = N, tg = X t/s` and `draft acceptance = P (A / G)` lines are
    the real-time needles — they tick during generation, not just at turn-end."""
    if d.get("type") != "runtime.log":
        return
    msg = d.get("message", "")
    if not msg:
        return
    ts = time.time()

    # 1) PREFILL (the reliable one, all prompt sizes):
    #   "prompt eval time = 1082 ms / 240 tokens ( 4.5 ms per token, 221.73 tokens per second)"
    for m in _RE_PROMPT_SUMMARY.finditer(msg):
        prefill_tps = float(m.group(2))
        mt = list(_RE_TASK.finditer(msg[:m.start()]))
        tid = mt[-1].group(1) if mt else "?"
        with LOCK:
            SERVER["prefill_tps"] = prefill_tps
            RUNTIME_PREFILL.append((ts, prefill_tps))
            RUNTIME_PREFILL_TASK[tid] = max(RUNTIME_PREFILL_TASK.get(tid, 0.0), prefill_tps)
            _session["sum_prefill"] += prefill_tps
            _session["n_prefill"] += 1
            _session["peak_prefill"] = max(_session["peak_prefill"], prefill_tps)
            _LAST_LIVE["ts"] = ts            # prefill is a fresh activity tick —
                                             # keeps live.stale covering ALL three
                                             # generation counters (decode/prefill/draft)

    # 2) LIVE prompt progress — only present for large prompts (batched prefill):
    #   "task 57647 | prompt processing, n_tokens = 14336, progress = 0.21, t = 15.95 s / 898.87 tokens per second"
    for m in _RE_PROMPT_LIVE.finditer(msg):
        tid = m.group(1)
        progress = float(m.group(3))
        with LOCK:
            SERVER["prompt_progress"] = progress
            RUNTIME_PROGRESS.append((ts, progress))
            RUNTIME_PROGRESS_TASK[tid] = max(RUNTIME_PROGRESS_TASK.get(tid, 0.0), progress)

    # 3) LIVE decode ticks (n_gen = N, tg = X t/s) — the real-time tok/s needle.
    #    n_gen is the running token count for this turn (live "context this turn"
    #    out-tokens); tg is the live decode speed.
    for m in _RE_TGS.finditer(msg):
        n_gen = int(m.group(1))
        tg = float(m.group(2))
        with LOCK:
            RUNTIME_TPS.append((ts, tg))
            RUNTIME_PRED.append((ts, n_gen))
            _LAST_LIVE["decode"] = tg
            _LAST_LIVE["pred"] = n_gen          # live tokens generated this turn
            _LAST_LIVE["ts"] = ts

    # 4) LIVE draft acceptance (A accepted / G generated) — live "draft acceptance"
    for m in _RE_DRAFT.finditer(msg):
        pct = float(m.group(1)) * 100.0
        acc = int(m.group(2))
        with LOCK:
            RUNTIME_DRAFT.append((ts, pct))
            _LAST_LIVE["draft_pct"] = round(pct, 1)
            _LAST_LIVE["acc"] = acc
            _LAST_LIVE["ts"] = ts

    # 5) task end (total time = ... ms / N tokens) — flush max progress + peak prefill
    #    for this task so the model event (arriving ~immediately after) can attach it.
    for m in _RE_END_SUMMARY.finditer(msg):
        mt = list(_RE_TASK.finditer(msg[:m.end()]))
        if mt:
            tid = mt[-1].group(1)
            with LOCK:
                prog = RUNTIME_PROGRESS_TASK.pop(tid, None)
                prefl = RUNTIME_PREFILL_TASK.pop(tid, None)
                if prog is not None or prefl:
                    _PENDING_PROGRESS.append((ts, prog or 0.0, prefl or 0.0))


# ----------------------------------------------------------------- streams
def model_stream_loop():
    if not LMS:
        return
    while not _dying:
        p = None
        try:
            p = subprocess.Popen([LMS, "log", "stream", "--json", "--stats",
                                 "--source", "model"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW, text=True, bufsize=1,
                                encoding="utf-8", errors="replace")
            with _STREAM_LOCK:
                _STREAMS["model"] = p
            log("model stream started")
            for line in p.stdout:
                if _dying:
                    break
                line = line.strip()
                if not line or "Streaming" in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                d = o.get("data", {}) if isinstance(o, dict) else {}
                if d.get("type") == "llm.prediction.output" and d.get("stats"):
                    record_event(make_event(o, d))
        except Exception as e:
            log("model stream error: %s" % e)
        finally:
            if p:
                _STREAMS.pop("model", None)
                try:
                    p.stdout.close()
                except Exception:
                    pass
                try:
                    p.kill()
                except Exception:
                    pass
        time.sleep(2)


def runtime_stream_loop():
    if not LMS:
        return
    while not _dying:
        p = None
        try:
            p = subprocess.Popen([LMS, "log", "stream", "--json", "--source", "runtime"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=NO_WINDOW, text=True, bufsize=1,
                                encoding="utf-8", errors="replace")
            with _STREAM_LOCK:
                _STREAMS["runtime"] = p
            log("runtime stream started")
            for line in p.stdout:
                if _dying:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                d = o.get("data", {}) if isinstance(o, dict) else {}
                parse_runtime_line(o, d)
        except Exception as e:
            log("runtime stream error: %s" % e)
        finally:
            if p:
                _STREAMS.pop("runtime", None)
                try:
                    p.stdout.close()
                except Exception:
                    pass
                try:
                    p.kill()
                except Exception:
                    pass
        time.sleep(2)


def _kill_streams():
    """Kill every live `lms log stream` child so nothing is orphaned on shutdown.

    Called from both the /api/exit path and main()'s finally block (idempotent).
    """
    with _STREAM_LOCK:
        procs = list(_STREAMS.values())
        _STREAMS.clear()
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    # give the OS a beat to reap them
    time.sleep(0.15)




_model_present = {"was": False, "t0": None}


def poll_loop(interval=2):
    while True:
        try:
            SERVER["server"] = server_status()
            SERVER["model"] = model_info()
            SERVER["gpu"] = gpu_stats()
            SERVER["ram"] = ram_stats()
            SERVER["asof"] = time.time()
            # "model loaded for X" — track the absent->present transition. This
            # is the true load-uptime (resets correctly if the model is swapped).
            m = SERVER["model"]
            present = bool(m and (m.get("modelKey") or m.get("identifier") or m.get("path")))
            now = time.time()
            with LOCK:
                if present and not _model_present["was"]:
                    _model_present["t0"] = now
                    _model_present["was"] = True
                    SERVER["uptime_sec"] = 0.0
                elif present and _model_present["t0"]:
                    SERVER["uptime_sec"] = max(0.0, now - _model_present["t0"])
                elif not present and _model_present["was"]:
                    _model_present["was"] = False
                    _model_present["t0"] = None
                    SERVER["uptime_sec"] = None
        except Exception as e:
            log("poll error: %s" % e)
        time.sleep(interval)


def lifetime_sampler(interval=30):
    """Append a (gpu, vram, ram, power) sample every 30s for lifetime graphs,
    plus the latest live inference perf (decode/prefill/draft) when present, so
    the perf graph also accumulates across 1d/7d/all. Each 30s span is also
    folded into the durable ledger so all-time energy survives archival."""
    _last_fold = {"ts": None}
    while True:
        g = SERVER.get("gpu")
        r = SERVER.get("ram")
        if g or r:
            rec = {"ts": time.time(),
                   "gpu_util": (g or {}).get("util_pct"),
                   "vram_pct": ((g["vram_used_mb"] / g["vram_total_mb"]) * 100) if g and g.get("vram_total_mb") else None,
                   "power_w": (g or {}).get("power_w"),
                   "ram_pct": (r or {}).get("pct")}
            # CPU util % for the lifetime CPU graph (single consumer of the
            # cpu_percent baseline; the 3s rolling sampler feeds ROLL_CPU).
            if ROLL_CPU and ROLL_CPU[-1][1] is not None:
                rec["cpu_util"] = ROLL_CPU[-1][1]
            # latest live inference perf (only when a generation produced a
            # FRESH tick this window, i.e. <60s old) — keeps the decode/prefill/
            # draft line persistent without drawing a misleading flat line at a
            # stale last value when no generation is running.
            with LOCK:
                now = time.time()
                dec = RUNTIME_TPS[-1] if RUNTIME_TPS else None
                pre = RUNTIME_PREFILL[-1] if RUNTIME_PREFILL else None
                dra = RUNTIME_DRAFT[-1] if RUNTIME_DRAFT else None
            for key, tup in (("decode_tps", dec), ("prefill_tps", pre), ("draft_pct", dra)):
                if tup and (now - tup[0]) < 60 and tup[1] is not None:
                    rec[key] = tup[1]
            try:
                with open(LIFETIME, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
                # fold the energy span since the last sample into the durable
                # ledger (skip the first loop — that span was already in the seed).
                if _last_fold["ts"] is not None and rec.get("power_w") is not None:
                    with _LEDGER_LOCK:
                        base_ts = _load_ledger().get("energy_last_ts")
                    base_ts = base_ts if base_ts else _last_fold["ts"]
                    if rec["ts"] > base_ts:
                        _ledger_add_energy(rec["ts"], rec["power_w"], rec["ts"] - base_ts)
            except Exception as e:
                log("lifetime write failed: %s" % e)
            _last_fold["ts"] = rec["ts"]
        time.sleep(interval)


# rolling in-memory sampler for the tile sparklines (3s cadence, ~1h window)
ROLL_GPU = deque(maxlen=3600)      # (ts, util%)
ROLL_VRAM = deque(maxlen=3600)     # (ts, vram%)
ROLL_RAM = deque(maxlen=3600)      # (ts, ram%)
ROLL_TEMP = deque(maxlen=3600)     # (ts, °C)
ROLL_POWER = deque(maxlen=3600)    # (ts, W)
ROLL_CPU = deque(maxlen=3600)      # (ts, cpu%) — single consumer of cpu_percent(None)


def rolling_sampler(interval=3):
    while True:
        try:
            ts = time.time()
            g = SERVER.get("gpu")
            r = SERVER.get("ram")
            if g:
                ROLL_GPU.append((ts, g.get("util_pct")))
                if g.get("vram_total_mb"):
                    ROLL_VRAM.append((ts, g["vram_used_mb"] / g["vram_total_mb"] * 100))
                if g.get("temp_c") is not None:
                    ROLL_TEMP.append((ts, g.get("temp_c")))
                if g.get("power_w") is not None:
                    ROLL_POWER.append((ts, g.get("power_w")))
            if r and r.get("pct") is not None:
                ROLL_RAM.append((ts, r["pct"]))
            if psutil is not None:
                # only place that calls cpu_percent(None) — keeps the "since last
                # call" baseline consistent (lifetime_sampler reads ROLL_CPU[-1]).
                c = psutil.cpu_percent(None)
                if c is not None:
                    ROLL_CPU.append((ts, round(c, 1)))
        except Exception as e:
            log("rolling sample error: %s" % e)
        time.sleep(interval)


# ------------------------------------------------------------------ series
RANGE_SEC = {"1m": 60, "5m": 300, "10m": 600, "15m": 900, "1h": 3600, "3h": 10800,
             "1d": 86400, "7d": 604800, "1mo": 2592000, "all": None}


def _bucketize(samples, now, range_sec, n_buckets=120):
    """samples: list[(ts, val)] -> list[(ts_center, val)].

    The X-axis is fitted to the *data's own* span (first->last sample), not the
    full requested range — so a fresh 30-min of lifetime data renders as a full
    line, and a 15-min sparkline with 30s of data doesn't smear into one sliver.
    `range_sec` only decides which samples are *included* (the lower bound)."""
    if not samples:
        return []
    samples = sorted(samples, key=lambda p: p[0])
    lo = samples[0][0]
    hi = max(samples[-1][0], now)
    span = max(1.0, hi - lo)
    out = []
    for i in range(n_buckets):
        b0 = lo + span * i / n_buckets
        b1 = lo + span * (i + 1) / n_buckets
        inb = [v for (t, v) in samples if b0 <= t < b1 + (1e-9 if i == n_buckets - 1 else 0)]
        if inb:
            out.append(((b0 + b1) / 2, inb[-1]))
    return out


def metric_series(metric, range_key):
    now = time.time()
    range_sec = RANGE_SEC.get(range_key, 900)
    lo = now - (range_sec or 0) if range_sec else 0
    with LOCK:
        if metric in ("tps", "ttft", "prompt"):
            # event-based (per-turn, from the model stream)
            evs = [e for e in EVENTS if e.get("ts") and e["ts"] >= lo and e.get(metric) is not None]
            samples = [(e["ts"], e[metric]) for e in evs]
        elif metric in ("gpu_util", "vram_pct", "temp", "power", "ram_pct", "cpu_util"):
            roll = {"gpu_util": ROLL_GPU, "vram_pct": ROLL_VRAM,
                    "temp": ROLL_TEMP, "power": ROLL_POWER,
                    "ram_pct": ROLL_RAM, "cpu_util": ROLL_CPU}.get(metric, ())
            if range_sec and range_sec < 86400:
                # short range -> rolling in-memory samples (3s cadence)
                samples = [(t, v) for (t, v) in roll if t >= lo]
            else:
                # long range -> lifetime file (30s cadence)
                samples = read_lifetime_metric(metric, lo)
                # tack on the most recent rolling tail so the curve reaches now,
                # but only the portion AFTER the last lifetime sample (no overlap)
                if len(samples) and roll:
                    last_life = samples[-1][0]
                    tail = [(t, v) for (t, v) in roll if t > last_life]
                    samples = samples + tail
                elif roll:
                    samples = [(t, v) for (t, v) in roll]
        elif metric == "prefill":
            samples = [(t, v) for (t, v) in RUNTIME_PREFILL if t >= lo]
        elif metric == "decode":
            # LIVE decode speed from the runtime stream (n_gen/tg ticks) —
            # this is the real-time tok/s needle, continuous during generation.
            samples = [(t, v) for (t, v) in RUNTIME_TPS if t >= lo]
        elif metric == "draft":
            # LIVE draft-acceptance % from the runtime stream
            samples = [(t, v) for (t, v) in RUNTIME_DRAFT if t >= lo]
        elif metric == "pred":
            # live tokens-generated-this-turn (n_gen) from runtime
            samples = [(t, v) for (t, v) in RUNTIME_PRED if t >= lo]
        elif metric == "progress":
            samples = [(t, v) for (t, v) in RUNTIME_PROGRESS if t >= lo]
        elif metric == "perf":
            # composite: 3 independent series for the inference-performance graph.
            # Each is bucketized over its OWN data span so the three lines
            # (decode ~40, prefill ~800, draft ~85%) each render legibly.
            # Short ranges read the live runtime deques; long ranges (>=1d) fall
            # back to the lifetime file (30s samples) + the live tail, so 7d/all
            # have real data instead of going blank after the 3h deque.
            def _b(lfkey, deq):
                if range_sec and range_sec >= 86400:
                    lfs = read_lifetime_metric(lfkey, lo)
                    tail = [(t, v) for (t, v) in deq if t >= lo]
                    if lfs and tail:
                        last_life = lfs[-1][0]
                        lfs = lfs + [(t, v) for (t, v) in tail if t > last_life]
                    elif not lfs:
                        lfs = tail
                    return [{"t": round(t, 1), "v": round(v, 2)}
                            for t, v in _bucketize(lfs, now, range_sec)]
                return [{"t": round(t, 1), "v": round(v, 2)}
                        for t, v in _bucketize([(t, v) for (t, v) in deq if t >= lo], now, range_sec)]
            return {"decode": _b("decode_tps", RUNTIME_TPS),
                    "prefill": _b("prefill_tps", RUNTIME_PREFILL),
                    "draft": _b("draft_pct", RUNTIME_DRAFT)}
        else:
            samples = []
    if range_sec:
        return _bucketize(samples, now, range_sec)
    return [(t, v) for (t, v) in samples]


def read_lifetime_metric(metric, lo):
    out = []
    if not os.path.exists(LIFETIME):
        return out
    key = {"gpu_util": "gpu_util", "vram_pct": "vram_pct",
           "power": "power_w", "ram_pct": "ram_pct", "cpu_util": "cpu_util",
           "decode_tps": "decode_tps", "prefill_tps": "prefill_tps",
           "draft_pct": "draft_pct"}.get(metric)
    try:
        with open(LIFETIME, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("ts", 0) < lo:
                    continue
                v = o.get(key)
                if v is not None:
                    out.append((o["ts"], v))
    except Exception:
        pass
    return out


def lifetime(days=30):
    lo = time.time() - days * 86400
    acc = {"gpu_util": [], "vram_pct": [], "power_w": [], "ram_pct": [], "cpu_util": []}
    if os.path.exists(LIFETIME):
        try:
            with open(LIFETIME, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("ts", 0) < lo:
                        continue
                    for k in acc:
                        if o.get(k) is not None:
                            acc[k].append(o[k])
        except Exception as e:
            log("lifetime read failed: %s" % e)
    def _st(xs):
        if not xs:
            return {"avg": None, "peak": None, "min": None, "n": 0}
        return {"avg": round(sum(xs) / len(xs), 1), "peak": round(max(xs), 1),
                "min": round(min(xs), 1), "n": len(xs)}
    return {"days": days, "gpu_util": _st(acc["gpu_util"]), "vram_pct": _st(acc["vram_pct"]),
            "power_w": _st(acc["power_w"]), "ram_pct": _st(acc["ram_pct"]),
            "cpu_util": _st(acc["cpu_util"]),
            "asof": time.time()}


def _lifetime_by_day():
    """Group lifetime.jsonl samples by calendar day (local time) for per-day rollups.

    Returns {day: {"gpu_util":[], "vram_pct":[], "power_w":[], "cpu_util":[],
    "ram_pct":[], "series": [(ts, power_w), ...]}}."""
    days = {}
    if not os.path.exists(LIFETIME):
        return days
    try:
        with open(LIFETIME, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = o.get("ts")
                if not ts:
                    continue
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                d = days.setdefault(day, {"gpu_util": [], "vram_pct": [], "power_w": [],
                                          "cpu_util": [], "ram_pct": [], "series": []})
                for k in ("gpu_util", "vram_pct", "cpu_util", "ram_pct"):
                    if o.get(k) is not None:
                        d[k].append(o[k])
                if o.get("power_w") is not None:
                    d["power_w"].append(o["power_w"])
                    d["series"].append((ts, o["power_w"]))
    except Exception as e:
        log("lifetime-by-day read failed: %s" % e)
    return days


def history(days=7):
    """Per-day rollup: generation stats (from history.jsonl) + hardware/energy
    averages (from lifetime.jsonl) + the money breakdown ($ spent, $ active,
    $ saved) + active time (model actually working)."""
    if not os.path.exists(HISTORY):
        return []
    cutoff = time.time() - days * 86400
    dm = {}
    with open(HISTORY, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ts = ev.get("ts") or 0
            if ts < cutoff:
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            r = dm.setdefault(day, {"gens": 0, "tokens": 0, "tokens_out": 0,
                                    "tps_sum": 0.0, "ttft_sum": 0.0, "n_tps": 0,
                                    "n_ttft": 0, "peak": 0.0})
            if ev.get("tps") is not None:
                r["gens"] += 1
                r["tps_sum"] += ev["tps"]
                r["n_tps"] += 1
                r["peak"] = max(r["peak"], ev["tps"])
            if ev.get("pred"):
                r["tokens"] += ev["pred"]
                r["tokens_out"] += ev["pred"]
            if ev.get("ttft") is not None:
                r["ttft_sum"] += ev["ttft"]
                r["n_ttft"] += 1
    ld = _lifetime_by_day()
    rate = CFG.get("cost_per_kwh", COST_PER_KWH)
    cpuw = CFG.get("cpu_w", CPU_W_ESTIMATE)
    idle = CFG.get("idle_power_w", IDLE_POWER_W)
    fout = CFG.get("frontier_out", FRONTIER_OUT)
    out = []
    for day in sorted(dm):
        r = dm[day]
        L = ld.get(day, {"gpu_util": [], "vram_pct": [], "power_w": [],
                         "cpu_util": [], "ram_pct": [], "series": []})
        def _avg(xs):
            return round(sum(xs) / len(xs), 1) if xs else None
        # energy for the day: integrate the day's power series
        all_j = act_j = 0.0
        act_sec = 0.0
        prev = None
        for (ts, pw) in sorted(L.get("series", [])):
            if prev is not None and ts > prev:
                dt = ts - prev
                all_j += (pw + cpuw) * dt
                if pw >= idle:
                    act_j += (pw + cpuw) * dt
                    act_sec += dt
            prev = ts
        all_kwh = all_j / JS_PER_KWH
        act_kwh = act_j / JS_PER_KWH
        day_cost = round(all_kwh * rate, 4)
        active_cost = round(act_kwh * rate, 4)
        frontier_cost = round(r["tokens_out"] * fout / 1_000_000.0, 2)
        saved = round(max(0.0, frontier_cost - active_cost), 2)
        out.append({
            "day": day, "gens": r["gens"], "tokens": r["tokens"],
            "avg_tps": round(r["tps_sum"] / r["n_tps"], 1) if r["n_tps"] else None,
            "peak_tps": round(r["peak"], 1) if r["peak"] else None,
            "avg_ttft_ms": round(r["ttft_sum"] / r["n_ttft"] * 1000, 0) if r["n_ttft"] else None,
            "gpu_util": _avg(L["gpu_util"]), "vram_pct": _avg(L["vram_pct"]),
            "cpu_util": _avg(L["cpu_util"]), "ram_pct": _avg(L["ram_pct"]),
            "power_w": _avg(L["power_w"]),
            "active_sec": round(act_sec, 0) if act_sec else 0,
            "active_min": round(act_sec / 60.0, 1) if act_sec else 0,
            "kwh": round(all_kwh, 4), "cost": day_cost,
            "active_cost": active_cost, "frontier_cost": frontier_cost,
            "saved": saved,
        })
    return out


# ---------------------------------------------------------------------- api
# ── durable all-time ledger (survives restarts AND archival) ────────────────
# history.jsonl + lifetime.jsonl are the *detailed* logs; they get rotated out
# of the live files into archive/ so they stay small. ledger.json holds the
# cumulative all-time aggregates so the true totals never shrink when we
# compact. It's seeded ONCE from the existing files, then updated live.
LEDGER = os.path.join(HERE, "ledger.json")
ARCHIVE = os.path.join(HERE, "archive")
_LEDGER_LOCK = threading.Lock()

def _empty_ledger():
    return {"seeded": False, "since_ts": time.time(),
            "tokens_all": 0, "tokens_in": 0, "tokens_out": 0, "gens": 0,
            "all_kwh": 0.0, "active_kwh": 0.0, "active_sec": 0.0, "total_sec": 0.0,
            "energy_last_ts": None, "hist_hi_ts": None}

def _load_ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        base = _empty_ledger()
        base.update({k: d[k] for k in base if k in d})
        return base
    except Exception:
        return _empty_ledger()

def _save_ledger(L):
    try:
        with open(LEDGER, "w", encoding="utf-8") as f:
            f.write(json.dumps(L, indent=2) + "\n")
    except Exception as e:
        log("ledger save failed: %s" % e)

def _integral_energy(lo=None):
    """(all_joules, active_joules, active_sec, total_sec, last_ts) over
    lifetime.jsonl samples with ts >= lo. A sample is 'active' when GPU draw
    >= idle threshold (the model is doing work, not just parked/loaded)."""
    all_j = act_j = 0.0
    act_sec = tot_sec = 0.0
    last_ts = None
    if not os.path.exists(LIFETIME):
        return 0.0, 0.0, 0.0, 0.0, None
    idle = CFG.get("idle_power_w", IDLE_POWER_W)
    cpuw = CFG.get("cpu_w", CPU_W_ESTIMATE)
    prev = None
    try:
        with open(LIFETIME, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = o.get("ts"); pw = o.get("power_w")
                if ts is None or pw is None:
                    continue
                if lo and ts < lo:
                    prev = ts
                    continue
                if prev is not None and ts > prev:
                    dt = ts - prev
                    all_j += (pw + cpuw) * dt
                    tot_sec += dt
                    if pw >= idle:
                        act_j += (pw + cpuw) * dt
                        act_sec += dt
                prev = ts
                last_ts = ts
    except Exception as e:
        log("energy integral failed: %s" % e)
    return all_j, act_j, act_sec, tot_sec, last_ts

def _history_totals(lo=None):
    """(tokens_all, tokens_in, tokens_out, gens, max_ts) over history.jsonl."""
    total = out = gens = 0
    max_ts = None
    if not os.path.exists(HISTORY):
        return 0, 0, 0, 0, None
    try:
        with open(HISTORY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts = ev.get("ts")
                if lo and ts and ts < lo:
                    continue
                if ev.get("tps") is None and not (ev.get("pred") or ev.get("prompt")):
                    continue
                gens += 1
                t = ev.get("total")
                if t is None:
                    t = (ev.get("prompt") or 0) + (ev.get("pred") or 0)
                total += t
                out += ev.get("pred") or 0
                if ts and (max_ts is None or ts > max_ts):
                    max_ts = ts
    except Exception as e:
        log("history totals failed: %s" % e)
    return total, max(0, total - out), out, gens, max_ts

def _ensure_ledger_locked():
    """Seed the ledger ONCE from the existing files, then no-op. Caller MUST
    already hold _LEDGER_LOCK (the lock is not reentrant — a self-acquire here
    would deadlock). Idempotent + guarded by the `seeded` flag, so a restart
    never re-seeds or double-counts."""
    L = _load_ledger()
    if L.get("seeded"):
        return L
    total, tin, tout, gens, max_ts = _history_totals()
    all_j, act_j, act_sec, tot_sec, last_ts = _integral_energy()
    L.update({
        "seeded": True, "since_ts": time.time(),
        "tokens_all": int(total), "tokens_in": int(tin), "tokens_out": int(tout),
        "gens": int(gens),
        "all_kwh": all_j / JS_PER_KWH, "active_kwh": act_j / JS_PER_KWH,
        "active_sec": act_sec, "total_sec": tot_sec,
        "energy_last_ts": last_ts, "hist_hi_ts": max_ts,
    })
    _save_ledger(L)
    log("ledger seeded: %d toks, %.3f kWh (%.3f active)" % (total, all_j / JS_PER_KWH, act_j / JS_PER_KWH))
    return L


def ensure_ledger():
    with _LEDGER_LOCK:
        return _ensure_ledger_locked()

def _ledger_add_event(ev):
    with _LEDGER_LOCK:
        L = _load_ledger()
        t = ev.get("total")
        if t is None:
            t = (ev.get("prompt") or 0) + (ev.get("pred") or 0)
        t = t or 0
        L["tokens_all"] += int(t)
        L["tokens_out"] += int(ev.get("pred") or 0)
        L["tokens_in"] += max(0, int(t) - int(ev.get("pred") or 0))
        if ev.get("tps") is not None:
            L["gens"] += 1
        ts = ev.get("ts")
        if ts and (L.get("hist_hi_ts") is None or ts > L["hist_hi_ts"]):
            L["hist_hi_ts"] = ts
        _save_ledger(L)

def _ledger_add_energy(ts, pw, dt):
    with _LEDGER_LOCK:
        L = _load_ledger()
        L["all_kwh"] += (pw + CFG.get("cpu_w", CPU_W_ESTIMATE)) * dt / JS_PER_KWH
        L["total_sec"] += dt
        if pw >= CFG.get("idle_power_w", IDLE_POWER_W):
            L["active_kwh"] += (pw + CFG.get("cpu_w", CPU_W_ESTIMATE)) * dt / JS_PER_KWH
            L["active_sec"] += dt
        L["energy_last_ts"] = ts
        _save_ledger(L)

def energy_cost():
    """All-time + this-session energy/cost.

    All-time comes from the durable ledger (O(1)). Session (since this server
    start) is integrated fresh from lifetime.jsonl over the small recent span.
    `active_kwh` is the portion drawn while the model was actually working
    (GPU draw >= idle threshold) — that's what $ Saved is priced against."""
    with _LEDGER_LOCK:
        L = _ensure_ledger_locked()
        all_kwh = L["all_kwh"]; active_kwh = L["active_kwh"]
        active_sec = L["active_sec"]; total_sec = L["total_sec"]
    # session = samples since this server started (small, direct integral)
    sj = 0.0
    if os.path.exists(LIFETIME):
        try:
            prev = None
            with open(LIFETIME, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    ts = o.get("ts"); pw = o.get("power_w")
                    if ts is None or pw is None:
                        prev = ts
                        continue
                    if ts >= PROCESS_START and prev is not None and ts > prev:
                        sj += (pw + CFG.get("cpu_w", CPU_W_ESTIMATE)) * (ts - prev)
                    prev = ts
        except Exception as e:
            log("session energy read failed: %s" % e)
    rate = CFG.get("cost_per_kwh", COST_PER_KWH)
    return {"rate": rate, "cpu_w": CFG.get("cpu_w", CPU_W_ESTIMATE),
            "all_kwh": round(all_kwh, 4), "all_cost": round(all_kwh * rate, 4),
            "active_kwh": round(active_kwh, 4), "active_cost": round(active_kwh * rate, 4),
            "active_sec": round(active_sec, 1), "total_sec": round(total_sec, 1),
            "sess_kwh": round(sj / JS_PER_KWH, 6), "sess_cost": round(sj / JS_PER_KWH * rate, 6)}

def totals():
    """All-time tokens (ledger) + energy/cost + $ Saved vs frontier.

    $ Saved = frontier price of THIS token output  −  electricity cost of the
    time the model was ACTUALLY working (active_kwh). Idle draw stays in the
    all-time $ figure but does not count against the "saved" comparison, per
    Nadia's spec — saving only makes sense for the work the model did."""
    with _LEDGER_LOCK:
        L = _ensure_ledger_locked()
        total = int(L["tokens_all"]); tin = int(L["tokens_in"]); tout = int(L["tokens_out"])
        gens = int(L["gens"])
    cost = energy_cost()
    fin = CFG.get("frontier_in", FRONTIER_IN)
    fout = CFG.get("frontier_out", FRONTIER_OUT)
    frontier_cost = (tin * fin + tout * fout) / 1_000_000.0
    saved = max(0.0, frontier_cost - cost["active_cost"])
    return {"tokens_all": total, "tokens_in": tin, "tokens_out": tout, "gens": gens,
            "asof": time.time(), "rate": cost["rate"], "cpu_w": cost["cpu_w"],
            "all_kwh": cost["all_kwh"], "all_cost": cost["all_cost"],
            "active_kwh": cost["active_kwh"], "active_cost": round(cost["active_cost"], 2),
            "active_sec": cost["active_sec"], "total_sec": cost["total_sec"],
            "sess_kwh": cost["sess_kwh"], "sess_cost": cost["sess_cost"],
            "frontier_in": fin, "frontier_out": fout,
            "frontier_cost": round(frontier_cost, 2), "saved": round(saved, 2)}


def data_sizes():
    """Live file sizes + archive size + retention setting (for the data panel)."""
    def _sz(p):
        try:
            return os.path.getsize(p)
        except Exception:
            return 0
    arch = 0
    if os.path.isdir(ARCHIVE):
        for fn in os.listdir(ARCHIVE):
            fp = os.path.join(ARCHIVE, fn)
            try:
                if os.path.isfile(fp):
                    arch += os.path.getsize(fp)
            except Exception:
                pass
    return {"lifetime_bytes": _sz(LIFETIME), "history_bytes": _sz(HISTORY),
            "ledger_bytes": _sz(LEDGER), "archive_bytes": arch,
            "lifetime_days": CFG.get("lifetime_days", 30.0)}


def compact():
    """Rotate samples older than `lifetime_days` out of the live files into
    archive/, keeping the live logs small. The durable ledger holds all-time
    totals, so we SUBTRACT the removed energy/tokens from it as we go — totals
    stay correct and the live files never grow unbounded."""
    cutoff = time.time() - CFG.get("lifetime_days", 30.0) * 86400
    os.makedirs(ARCHIVE, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    moved = {"lifetime": 0, "history": 0}
    # --- lifetime.jsonl: keep recent, archive old, subtract energy from ledger ---
    if os.path.exists(LIFETIME):
        keep, old = [], []
        with open(LIFETIME, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    old.append(line); continue
                (old if o.get("ts", 0) < cutoff else keep).append(line)
        if old:
            with open(os.path.join(ARCHIVE, "lifetime_%s.jsonl" % stamp), "a", encoding="utf-8") as f:
                f.write("\n".join(old) + "\n")
            moved["lifetime"] = len(old)
        with open(LIFETIME, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + ("\n" if keep else ""))
        # subtract the archived energy span from the ledger (avoid double-count)
        if old:
            with _LEDGER_LOCK:
                L = _load_ledger()
                all_j = act_j = act_sec = tot_sec = 0.0
                prev = None
                for line in old:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    ts = o.get("ts"); pw = o.get("power_w")
                    if ts is None or pw is None:
                        prev = ts; continue
                    if prev is not None and ts > prev:
                        dt = ts - prev
                        all_j += (pw + CFG.get("cpu_w", CPU_W_ESTIMATE)) * dt
                        tot_sec += dt
                        if pw >= CFG.get("idle_power_w", IDLE_POWER_W):
                            act_j += (pw + CFG.get("cpu_w", CPU_W_ESTIMATE)) * dt
                            act_sec += dt
                    prev = ts
                L["all_kwh"] = max(0.0, L["all_kwh"] - all_j / JS_PER_KWH)
                L["active_kwh"] = max(0.0, L["active_kwh"] - act_j / JS_PER_KWH)
                L["active_sec"] = max(0.0, L["active_sec"] - act_sec)
                L["total_sec"] = max(0.0, L["total_sec"] - tot_sec)
                _save_ledger(L)
    # --- history.jsonl: keep recent, archive old, subtract tokens from ledger ---
    if os.path.exists(HISTORY):
        keep, old = [], []
        with open(HISTORY, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    ts = ev.get("ts") or 0
                except Exception:
                    old.append(line); continue
                (old if ts < cutoff else keep).append(line)
        if old:
            with open(os.path.join(ARCHIVE, "history_%s.jsonl" % stamp), "a", encoding="utf-8") as f:
                f.write("\n".join(old) + "\n")
            moved["history"] = len(old)
        with open(HISTORY, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + ("\n" if keep else ""))
        if old:
            with _LEDGER_LOCK:
                L = _load_ledger()
                # tokens in archived events (only ones that counted)
                dtok = dout = dgens = 0
                for line in old:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("tps") is None and not (ev.get("pred") or ev.get("prompt")):
                        continue
                    dgens += 1
                    t = ev.get("total")
                    if t is None:
                        t = (ev.get("prompt") or 0) + (ev.get("pred") or 0)
                    dtok += t
                    dout += ev.get("pred") or 0
                L["tokens_all"] = max(0, int(L["tokens_all"]) - int(dtok))
                L["tokens_out"] = max(0, int(L["tokens_out"]) - int(dout))
                L["tokens_in"] = max(0, int(L["tokens_in"]) - max(0, int(dtok) - int(dout)))
                L["gens"] = max(0, int(L["gens"]) - dgens)
                _save_ledger(L)
    return {"ok": True, "moved": moved, "cutoff": cutoff, "sizes": data_sizes()}


def self_query():
    try:
        return parse_qs(urlparse(Handler.path).query)
    except Exception:
        return {}


def api_payload(route, method, body=None):
    q = self_query()
    if route == "/api/status":
        with LOCK:
            ev = SERVER.get("last_event")
            sess = dict(_session)
            # live runtime needles (decode t/s, tokens-this-turn, draft %) — only
            # exposed if they ticked within the last 12s, so they read as "live"
            # and go quiet (not frozen) between generations.
            now = time.time()
            ll = dict(_LAST_LIVE)
            if ll.get("ts") and now - ll["ts"] > 12.0:
                ll["stale"] = True
            # prompt progress freshness (same 12s window)
            prog_last = RUNTIME_PROGRESS[-1] if RUNTIME_PROGRESS else None
            prog_fresh = bool(prog_last and (now - prog_last[0]) < 12.0)
            # peak/avg over the rolling runtime window (session-lifetime)
            rt_decode = [v for (_t, v) in RUNTIME_TPS]
            rt_draft = [v for (_t, v) in RUNTIME_DRAFT]
            rt_pred_max = max((v for (_t, v) in RUNTIME_PRED), default=0)
        return {"server": SERVER.get("server"), "model": SERVER.get("model"),
                "gpu": SERVER.get("gpu"), "ram": SERVER.get("ram"),
                "prefill_tps": SERVER.get("prefill_tps"),
                "prompt_progress": SERVER.get("prompt_progress"),
                "progress_fresh": prog_fresh,
                "uptime_sec": SERVER.get("uptime_sec"),
                "version": VERSION,
                "last_event": ev, "session": sess, "asof": SERVER.get("asof"),
                "dying": _dying,
                "live": {"decode": ll.get("decode"), "pred": ll.get("pred"),
                         "draft_pct": ll.get("draft_pct"), "acc": ll.get("acc"),
                         "stale": ll.get("stale", False)},
                "runtime": {"decode_n": len(rt_decode),
                            "decode_avg": round(sum(rt_decode) / len(rt_decode), 1) if rt_decode else None,
                            "decode_peak": round(max(rt_decode), 1) if rt_decode else None,
                            "draft_n": len(rt_draft),
                            "draft_avg": round(sum(rt_draft) / len(rt_draft), 1) if rt_draft else None,
                            "draft_peak": round(max(rt_draft), 1) if rt_draft else None,
                            "pred_max": rt_pred_max}}
    if route == "/api/events":
        n = 200
        try:
            n = max(1, min(500, int((q.get("n") or ["200"])[0])))
        except Exception:
            n = 200
        with LOCK:
            evs = list(EVENTS)[:n]
        return {"events": evs, "asof": time.time()}
    if route == "/api/series":
        metric = (q.get("metric") or ["tps"])[0]
        range_key = (q.get("range") or ["15m"])[0]
        return {"metric": metric, "range": range_key,
                "points": metric_series(metric, range_key), "asof": time.time()}
    if route == "/api/lifetime":
        days = 30
        try:
            days = max(1, min(365, int((q.get("days") or ["30"])[0])))
        except Exception:
            days = 30
        return lifetime(days)
    if route == "/api/history":
        days = 7
        try:
            days = max(1, min(90, int((q.get("days") or ["7"])[0])))
        except Exception:
            days = 7
        return {"days": history(days), "asof": time.time()}
    if route == "/api/totals":
        return totals()
    if route == "/api/settings":
        return get_settings()
    if route == "/api/data":
        return data_sizes()
    if route == "/api/compact":
        if method != "POST":
            raise ApiError(405, "compact is POST")
        return compact()
    raise ApiError(404, "no such endpoint")


class ApiError(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


class Handler(BaseHTTPRequestHandler):
    server_version = "LMSViewport/%s" % VERSION
    path = "/"

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, payload):
        self._send(code, payload, "application/json")

    def do_GET(self):
        Handler.path = self.path
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._json(500, {"error": "index.html missing"})
            return
        if self._maybe_static(u.path):
            return
        try:
            self._json(200, api_payload(u.path, "GET"))
        except ApiError as e:
            self._json(e.code, {"error": e.msg})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _maybe_static(self, path):
        """Serve a whitelisted static asset (.js/.css) from HERE.

        Traversal-guarded: the resolved path must stay inside HERE and the
        extension must be one we allow. Returns True if handled. (The charts
        are now self-contained SVG — no external JS/CSS is loaded — so this
        is a generic fallback kept in case a static asset is ever added.)
        """
        name = os.path.basename(path)
        if not name or name == path:      # no real filename (e.g. "/")
            return False
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".js", ".css"):
            return False
        full = os.path.realpath(os.path.join(HERE, name))
        root = os.path.realpath(HERE)
        if not (full == root or full.startswith(root + os.sep)):
            self._json(403, {"error": "forbidden path"})
            return True
        try:
            with open(full, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self._json(404, {"error": "not found: %s" % name})
            return True
        ctype = {".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}[ext]
        self._send(200, data, ctype)
        return True

    def do_POST(self):
        Handler.path = self.path
        if self.path == "/api/settings":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except Exception:
                n = 0
            body = self.rfile.read(n) if n else b""
            try:
                d = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._json(400, {"error": "invalid JSON body"})
                return
            try:
                self._json(200, set_settings(d))
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if self.path == "/api/exit":
            global _dying
            if _dying:
                self._json(200, {"ok": True, "note": "already shutting down"})
                return
            _dying = True
            self._json(200, {"ok": True, "note": "viewport exiting — model/server untouched"})
            log("viewport exiting — releasing :%d" % PORT)

            def _stop():
                # Let the stream loops notice _dying and release their children
                # (their `for line in p.stdout` unblocks once the child dies), then
                # stop the accept loop via the CORRECT cross-thread API. server_close()
                # here was what produced the WinError 10038 traceback.
                time.sleep(0.3)
                _kill_streams()
                time.sleep(0.2)
                if _SERVER is not None:
                    try:
                        _SERVER.shutdown()   # signals serve_forever() to return
                    except Exception:
                        pass
            threading.Timer(0.2, _stop).start()
            return
        try:
            self._json(200, api_payload(self.path, "POST"))
        except ApiError as e:
            self._json(e.code, {"error": e.msg})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass


def main():
    global _SERVER
    log("=== viewport v2 starting on 127.0.0.1:%d (lms=%s, psutil=%s) ==="
        % (PORT, LMS, bool(psutil)))
    _load_settings()   # apply any user-tweaked knobs from settings.json
    ensure_ledger()    # seed the durable all-time ledger once (idempotent)
    for tgt, name in [(model_stream_loop, "model-stream"),
                      (runtime_stream_loop, "runtime-stream"),
                      (poll_loop, "poll"),
                      (rolling_sampler, "rolling"),
                      (lifetime_sampler, "lifetime")]:
        threading.Thread(target=tgt, daemon=True, name=name).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    _SERVER = srv
    log("listening on 127.0.0.1:%d" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Clean shutdown: kill child lms streams, close the accept socket, then
        # hard-exit so any lingering daemon threads can't keep the process (or the
        # port) alive. os._exit here is SAFE — the socket is already closed and the
        # children already killed, so nothing is leaked (this is NOT the old
        # mid-serve_forever os._exit that caused the WinError 10038).
        _kill_streams()
        try:
            srv.server_close()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
