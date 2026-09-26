#!/usr/bin/env bash
# Run the RTL-SDR sweeper against real dongles.
#
# This machine's SYSTEM librtlsdr is too old (missing rtlsdr_set_dithering),
# so we point pyrtlsdr at the newer librtlsdr built into ~/.local via
# LD_LIBRARY_PATH. pyrtlsdr resolves librtlsdr through ctypes, so this is
# sufficient -- no root and no system changes required.
#
# Examples:
#   ./run_sweeper.sh --sweep --device 1                 # FM band sweep on dongle 1
#   ./run_sweeper.sh --sweep --device 0 --start 118 --stop 137 --step 25   # airband
#   ./run_sweeper.sh --device 1                         # interactive GUI (needs pyqtgraph)
set -euo pipefail

export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
cd "$(dirname "$0")"

exec .venv/bin/python freq_sweeper_advanced.py "$@"
