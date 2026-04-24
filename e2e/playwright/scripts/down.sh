#!/usr/bin/env bash
# Stop the background Django server that up.sh launched. Idempotent.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$HERE/../.server.pid"

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo ">>> stopping APIS e2e server (pid $PID)"
    kill "$PID" || true
    # give it a moment to exit cleanly
    for _ in $(seq 1 10); do
      if ! kill -0 "$PID" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" || true
    fi
  fi
  rm -f "$PIDFILE"
fi
