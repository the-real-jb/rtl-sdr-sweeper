"""Pick interesting hits from one hop's FFT spectrum.

Owns median-floor detection, DC-spur rejection, hunt prioritization, and hit
ranking. Pure functions only; no dongle or thread concerns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bands import classify, hunt_match

_DC_TOLERANCE_HZ = 25_000.0
_MERGE_TOLERANCE_HZ = 50_000.0
_NARROW_FM_BW_HZ = 50_000.0
_BW_DB = 6.0

_REASON_ORDER = {"hunt": 0, "narrow_in_wide": 1, "voice": 2, "active": 3}


@dataclass
class Hit:
    freq_hz: float
    power_db: float
    bandwidth_hz: float
    label: str
    reason: str
    is_new: bool


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _run_bandwidth_hz(
    freq_hz: np.ndarray, power_db: np.ndarray, start: int, end: int, peak_idx: int
) -> float:
    peak_power = power_db[peak_idx]
    in_run = np.arange(start, end)
    within = in_run[power_db[in_run] >= peak_power - _BW_DB]
    if within.size == 0:
        return 0.0
    return float(freq_hz[within[-1]] - freq_hz[within[0]])


def _hit_reason(label: str, bandwidth_hz: float, freq_hz: float) -> str:
    if hunt_match(freq_hz):
        return "hunt"
    if label == "fm_broadcast" and bandwidth_hz < _NARROW_FM_BW_HZ:
        return "narrow_in_wide"
    if label in ("airband_am", "nfm_voice"):
        return "voice"
    return "active"


def hits_from_spectrum(
    freq_hz: np.ndarray,
    power_db: np.ndarray,
    hop_center_hz: float,
    threshold_db: float = 5.0,
) -> list[Hit]:
    floor = float(np.median(power_db)) + threshold_db
    above = power_db >= floor
    hits: list[Hit] = []

    for start, end in _contiguous_runs(above):
        local_peak = int(np.argmax(power_db[start:end]))
        peak_idx = start + local_peak
        peak_freq = float(freq_hz[peak_idx])
        peak_power = float(power_db[peak_idx])
        width_bins = end - start

        # DC spur at the hop center unless this is a watch frequency.
        if abs(peak_freq - hop_center_hz) <= _DC_TOLERANCE_HZ and not hunt_match(
            peak_freq
        ):
            continue
        if width_bins < 2 and not hunt_match(peak_freq):
            continue

        bandwidth_hz = _run_bandwidth_hz(freq_hz, power_db, start, end, peak_idx)
        label = classify(peak_freq)
        reason = _hit_reason(label, bandwidth_hz, peak_freq)
        hits.append(
            Hit(
                freq_hz=peak_freq,
                power_db=peak_power,
                bandwidth_hz=bandwidth_hz,
                label=label,
                reason=reason,
                is_new=False,
            )
        )

    return _merge_hits(hits)


def _merge_hits(hits: list[Hit]) -> list[Hit]:
    if not hits:
        return []
    remaining = sorted(hits, key=lambda h: -h.power_db)
    merged: list[Hit] = []
    for hit in remaining:
        if any(abs(hit.freq_hz - kept.freq_hz) < _MERGE_TOLERANCE_HZ for kept in merged):
            continue
        merged.append(hit)
    return merged


def sort_hits(hits: list[Hit]) -> list[Hit]:
    return sorted(
        hits,
        key=lambda h: (_REASON_ORDER.get(h.reason, 99), -h.power_db),
    )
