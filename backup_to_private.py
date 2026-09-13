#!/usr/bin/env python3
"""One-click off-site backup for LM Viewport.

Copies the LIVE viewport folder (code + your telemetry data) into the local
backup clone, then commits and pushes it to your PRIVATE repo. Your data never
touches the public repo — the public clone's .gitignore keeps it out.

  double-click run_backup.bat   (or:  python backup_to_private.py)

Safe to run any time; only pushes when there's actually a change.
"""
import os, sys, shutil, subprocess, datetime

HERE   = os.path.dirname(os.path.abspath(__file__))          # live folder
BASE   = os.path.dirname(HERE)                               # S:\Local.ai
LIVE   = HERE
BACKUP = os.path.join(BASE, "lmstudio-viewport-backup")      # private clone

# Never sync these into the backup (their .git points elsewhere / regenerable)
SKIP_DIRS  = {".git", "__pycache__", ".venv", "venv"}
SKIP_FILES = {"viewport.log", ".gitignore"}  # keep the backup's OWN .gitignore (it includes data)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def sync():
    os.makedirs(BACKUP, exist_ok=True)
    n = 0
    for name in os.listdir(LIVE):
        if name in SKIP_DIRS or name in SKIP_FILES:
            continue
        src, dst = os.path.join(LIVE, name), os.path.join(BACKUP, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_DIRS))
            n += 1
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
            n += 1
    return n


def push():
    def g(*args):
        return run("git", "-C", BACKUP, *args)
    st = g("status", "--porcelain")
    if not st.stdout.strip():
        print("  (no changes to back up)")
    else:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        g("add", "-A")
        r = g("commit", "-m", "backup %s" % ts)
        if r.returncode != 0:
            print("  commit failed:", r.stderr.strip())
    r = g("push", "origin", "HEAD")
    out = (r.stdout or "").strip() + " " + (r.stderr or "").strip()
    if out:
        print("   " + out.replace("\n", "\n   "))
    return r.returncode


def main():
    print("LM Viewport -> private off-site backup")
    print("  live   :", LIVE)
    print("  backup :", BACKUP)
    if not os.path.isdir(os.path.join(BACKUP, ".git")):
        print("ERROR: backup folder isn't a git clone yet — run the initial setup once.")
        return 1
    n = sync()
    print("  synced %d item(s)." % n)
    print("  pushing to private repo...")
    ok = push() == 0
    print("  " + ("OK - backup pushed" if ok else "FAILED - see git output above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
