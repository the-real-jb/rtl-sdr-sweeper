import numpy as np
from hits import hits_from_spectrum, sort_hits


def _flat(n, center, fs=2_400_000):
    freqs = center + np.linspace(-fs / 2, fs / 2, n, endpoint=False)
    power = np.full(n, -100.0)
    return freqs, power


def test_floor_and_single_bin_spur():
    freqs, power = _flat(128, 100e6)
    power[40] = -90.0  # 10 dB up, one bin
    assert hits_from_spectrum(freqs, power, hop_center_hz=100e6) == []


def test_dc_spur_dropped():
    freqs, power = _flat(128, 100e6)
    mid = int(np.argmin(np.abs(freqs - 100e6)))
    power[mid - 1 : mid + 2] = -70.0
    assert hits_from_spectrum(freqs, power, hop_center_hz=100e6) == []


def test_narrow_in_fm_band_and_hunt_sort():
    freqs, power = _flat(256, 100e6)
    # Narrow cluster away from DC, ~20 kHz class.
    power[20:23] = -80.0
    hits = hits_from_spectrum(freqs, power, hop_center_hz=100e6)
    assert len(hits) == 1
    assert hits[0].reason == "narrow_in_wide"
    assert hits[0].is_new is False


def test_hunt_beats_active_and_merge():
    freqs, power = _flat(512, 145e6, fs=2_400_000)
    # Two close bumps around APRS.
    i = int(np.argmin(np.abs(freqs - 144.390e6)))
    power[i : i + 3] = -70.0
    power[i + 4 : i + 7] = -75.0
    hits = sort_hits(hits_from_spectrum(freqs, power, hop_center_hz=145e6))
    assert hits[0].reason == "hunt"
    assert len(hits) == 1
