"""Plan overlapping frequency hops and merge multi-hop hit lists.

Hops overlap by 20 percent because tuner filters roll off at the edges.
Pure functions only; USB access stays in callers that pass a sweeper object.
"""

from __future__ import annotations

import numpy as np

from hits import Hit, hits_from_spectrum, sort_hits

_MERGE_TOLERANCE_HZ = 50_000.0


def hop_centers(start_hz: float, stop_hz: float, sample_rate: float) -> list[float]:
    half = sample_rate / 2.0
    step = sample_rate * 0.8
    centers: list[float] = []
    center = start_hz + half
    while True:
        centers.append(center)
        if center + half >= stop_hz:
            break
        center += step
    return centers


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


def scan_spectrum(
    sweep_power: list[tuple[np.ndarray, np.ndarray, float]],
) -> list[Hit]:
    hits: list[Hit] = []
    for freq_hz, power_db, hop_center_hz in sweep_power:
        hits.extend(hits_from_spectrum(freq_hz, power_db, hop_center_hz))
    return sort_hits(_merge_hits(hits))
