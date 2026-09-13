#!/usr/bin/env bash
# LM Studio Telemetry Viewport — Unix launcher (Linux / macOS)
#
# · starts the viewport with no terminal dependency (runs in background)
# · if it's already running, just opens the browser (no duplicate server)
# · needs: Python 3.8+ on PATH (and LM Studio installed). psutil is optional.
#
# Stop it from the dashboard's "close" button, or:  pkill -f dashboard.py
set -euo pipefail
cd "$(dirname "$0")"

PORT="${VIEWPORT_PORT:-18022}"
PY="$(command -v python3 || command -v python)"

# already serving? -> just open the browser, done
if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
  exec 3>&- 3<&-
  echo "Viewport already running -> opening http://127.0.0.1:$PORT"
  case "${OSTYPE:-}" in
    darwin*) open "http://127.0.0.1:$PORT" ;;
    linux*)  xdg-open "http://127.0.0.1:$PORT" 2>/dev/null || echo "open http://127.0.0.1:$PORT" ;;
    *)       echo "open http://127.0.0.1:$PORT" ;;
  esac
  exit 0
fi

# not running -> launch it, detached
nohup "$PY" dashboard.py >/dev/null 2>&1 &
echo "Started viewport (pid $!). Opening http://127.0.0.1:$PORT"
sleep 2
case "${OSTYPE:-}" in
  darwin*) open "http://127.0.0.1:$PORT" ;;
  linux*)  xdg-open "http://127.0.0.1:$PORT" 2>/dev/null || true ;;
esac
