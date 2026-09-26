import numpy as np
from demod import demodulate


def test_silence_and_empty():
    assert demodulate(np.zeros(0, dtype=np.complex64), 48_000, "nfm").size == 0
    pcm = demodulate(np.zeros(4800, dtype=np.complex64), 48_000, "am")
    assert pcm.dtype == np.int16
    assert np.max(np.abs(pcm)) == 0


def test_am_tone_near_1khz():
    sr = 48_000
    t = np.arange(sr) / sr
    carrier = np.exp(1j * 2 * np.pi * 0 * t)  # baseband
    iq = (1.0 + 0.6 * np.sin(2 * np.pi * 1000 * t)) * carrier
    pcm = demodulate(iq.astype(np.complex64), sr, "am")
    spec = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    peak = np.argmax(spec[1:]) + 1
    hz = peak * (48_000 / len(pcm))
    assert 800 < hz < 1200


def test_nfm_tone_near_1khz():
    sr = 48_000
    t = np.arange(sr) / sr
    mod = np.sin(2 * np.pi * 1000 * t)
    phase = np.cumsum(2 * np.pi * 2500 * mod / sr)
    iq = np.exp(1j * phase).astype(np.complex64)
    pcm = demodulate(iq, sr, "nfm")
    spec = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    peak = np.argmax(spec[1:]) + 1
    hz = peak * (48_000 / len(pcm))
    assert 800 < hz < 1200
