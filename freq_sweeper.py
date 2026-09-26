#!/usr/bin/env python3
"""Enhanced RTL-SDR Sweeper with FFT Spectrum Analysis.

Compatibility shim. The real implementation lives in ``freq_sweeper_advanced``
(the ``AdvancedSweeper`` subclass) and ``freq_sweeper_full`` (the
``RtlSDRSweeper`` base class). This module simply re-exports them so existing
imports of ``freq_sweeper`` keep working without maintaining a second copy.
"""

from freq_sweeper_advanced import AdvancedSweeper, interactive_sweep
from freq_sweeper_full import RtlSDRSweeper, SignalSample, FrequencyBand

__all__ = [
    "AdvancedSweeper",
    "interactive_sweep",
    "RtlSDRSweeper",
    "SignalSample",
    "FrequencyBand",
]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RTL-SDR sweeper (shim)")
    parser.add_argument("--device", type=int, default=0,
                        help="RTL-SDR device index (0 or 1)")
    args = parser.parse_args()
    interactive_sweep(args.device)
