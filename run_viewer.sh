#!/usr/bin/env bash
# Launch the browser-based RTL-SDR spectrum/waterfall viewer.
#
# Sets LD_LIBRARY_PATH so pyrtlsdr loads the newer librtlsdr in ~/.local
# (the system one lacks rtlsdr_set_dithering). Then open the printed URL
# (default http://127.0.0.1:8765) in your browser.
#
# Examples:
#   ./run_viewer.sh --device 1                 # viewer on dongle 1
#   ./run_viewer.sh --device 0 --port 8080
set -euo pipefail

export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
cd "$(dirname "$0")"

exec .venv/bin/python signal_viewer.py "$@"
