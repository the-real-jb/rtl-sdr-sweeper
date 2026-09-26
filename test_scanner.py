from scanner import hop_centers, scan_spectrum
import numpy as np


def test_hops_cover_activity_band():
    centers = hop_centers(118e6, 174e6, 2_400_000)
    assert centers[0] == 118e6 + 1_200_000
    assert centers[-1] + 1_200_000 >= 174e6


def test_scan_spectrum_sorts_hunt_first():
    freqs = np.linspace(144e6, 146e6, 64, endpoint=False)
    power = np.full(64, -110.0)
    i = int(np.argmin(np.abs(freqs - 144.390e6)))
    power[i : i + 3] = -80.0
    hits = scan_spectrum([(freqs, power, 145e6)])
    assert hits[0].reason == "hunt"
