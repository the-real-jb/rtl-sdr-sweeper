from math import gcd

import numpy as np
from scipy import signal

OUTPUT_RATE = 48_000
INT16_PEAK = int(0.8 * 32767)

MODE_CUTOFF = {
    "am": 5_000,
    "nfm": 4_000,
    "wfm": 15_000,
}


def demodulate(iq: np.ndarray, sample_rate: float, mode: str) -> np.ndarray:
    if iq.size == 0:
        return np.array([], dtype=np.int16)

    if mode in ("nfm", "wfm"):
        baseband = np.angle(iq[1:] * np.conj(iq[:-1]))
    elif mode == "am":
        baseband = np.abs(iq)
        baseband = baseband - np.mean(baseband)
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    baseband = np.nan_to_num(baseband)

    cutoff = MODE_CUTOFF[mode]
    nyquist = sample_rate / 2
    wn = min(cutoff / nyquist, 0.99)
    b, a = signal.butter(4, wn, btype="low")
    filtered = signal.filtfilt(b, a, baseband)

    up = int(OUTPUT_RATE)
    down = int(sample_rate)
    g = gcd(up, down)
    pcm_f = signal.resample_poly(filtered, up // g, down // g)

    peak = np.max(np.abs(pcm_f))
    if peak > 0:
        pcm_f = pcm_f * (INT16_PEAK / peak)
    else:
        pcm_f = np.zeros_like(pcm_f)

    return pcm_f.astype(np.int16)
