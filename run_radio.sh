#!/usr/bin/env bash
# Radio backend: serve the localhost API or run the CLI client.
#
# With no subcommand, starts radio_service on 127.0.0.1 (default port 8766).
# scan / listen / play are forwarded to radio.py (HTTP client, no dongle in-process).
set -euo pipefail

export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
cd "$(dirname "$0")"

case "${1:-}" in
  scan|listen|play)
    exec .venv/bin/python radio.py "$@"
    ;;
esac

DEVICE=0
PORT=8766
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      DEVICE="${2:?}"
      shift 2
      ;;
    --port)
      PORT="${2:?}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

exec .venv/bin/python - "$DEVICE" "$PORT" <<'PY'
import signal
import sys
import time

from radio_service import RadioService

device = int(sys.argv[1])
port = int(sys.argv[2])
svc = RadioService(device_index=device)
svc.start(port=port)
print(f"Radio service listening on http://127.0.0.1:{port} (device {device})", flush=True)


def shutdown(*_):
    svc.stop()
    raise SystemExit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)
while True:
    time.sleep(3600)
PY
