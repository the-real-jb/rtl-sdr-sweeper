#!/usr/bin/env python3
"""Lightweight pytest suite for the pure DSP/logic of the RTL-SDR sweeper.

These tests never touch real hardware: the base class constructor does not open
a device, the DSP helpers are pure, and where a device is needed we monkeypatch
the RtlSDR class with a fake.
"""

import time

import numpy as np
import pytest

import freq_sweeper_full as fsf
from freq_sweeper_full import RtlSDRSweeper, SignalSample
from freq_sweeper_advanced import AdvancedSweeper


def test_frequency_steps_include_stop_only_when_it_lands_on_a_step():
    """Sweep tunes start, then each step, and stops at the last step that does not pass stop.

    Dropping the exact stop (``<=`` becoming ``<``) loses 89 MHz.
    Including a tune past stop adds 88.0 MHz to the second grid.
    """
    from freq_sweeper_full import frequency_steps

    assert frequency_steps(88_000_000, 89_000_000, 500_000) == [
        88_000_000.0,
        88_500_000.0,
        89_000_000.0,
    ]
    assert frequency_steps(87_500_000, 88_000_000, 200_000) == [
        87_500_000.0,
        87_700_000.0,
        87_900_000.0,
    ]


# --------------------------------------------------------------------------- #
# get_signal_power (dBm estimate)
# --------------------------------------------------------------------------- #

def test_signal_power_unit_reference():
    """avg_power == 1 -> 10*log10(1) == 0 -> exactly the noise-floor constant."""
    s = RtlSDRSweeper()
    samples = np.ones(1000, dtype=complex)  # |s|^2 == 1
    assert s.get_signal_power(samples) == pytest.approx(RtlSDRSweeper.NOISE_FLOOR_DBM)


def test_signal_power_scales_with_amplitude():
    """A larger amplitude yields a higher dBm estimate; +10x power == +10 dB."""
    s = RtlSDRSweeper()
    weak = s.get_signal_power(np.full(1000, 1.0, dtype=complex))         # power 1
    strong = s.get_signal_power(np.full(1000, np.sqrt(10), dtype=complex))  # power 10
    assert strong - weak == pytest.approx(10.0, abs=1e-9)


def test_signal_power_empty_is_finite():
    """Empty buffer must not blow up with log10(0) -> returns the floor."""
    s = RtlSDRSweeper()
    val = s.get_signal_power(np.array([], dtype=complex))
    assert np.isfinite(val)
    assert val == pytest.approx(RtlSDRSweeper.NOISE_FLOOR_DBM)


def test_signal_power_all_zero_is_finite():
    s = RtlSDRSweeper()
    val = s.get_signal_power(np.zeros(500, dtype=complex))
    assert np.isfinite(val)
    assert val == pytest.approx(RtlSDRSweeper.NOISE_FLOOR_DBM)


# --------------------------------------------------------------------------- #
# analyze_with_fft (bin/frequency mapping)
# --------------------------------------------------------------------------- #

def _make_sweeper(fft_samples=1024, sample_rate=2_400_000, center_freq=100e6):
    s = AdvancedSweeper(fft_samples=fft_samples, sample_rate=sample_rate)
    s.center_freq = center_freq
    return s


def test_fft_peak_maps_to_correct_hz():
    """A complex tone at +offset should peak at center_freq + offset (within 1 bin)."""
    fft_n = 1024
    fs = 2_400_000
    fc = 100e6
    offset = 300_000.0  # +300 kHz baseband tone
    s = _make_sweeper(fft_samples=fft_n, sample_rate=fs, center_freq=fc)

    n = np.arange(fft_n)
    tone = np.exp(2j * np.pi * offset * n / fs)

    result = s.analyze_with_fft(tone)
    bin_hz = fs / fft_n
    assert result['peak_frequency_hz'] == pytest.approx(fc + offset, abs=bin_hz)


def test_fft_negative_offset_maps_below_center():
    fft_n = 1024
    fs = 2_400_000
    fc = 100e6
    offset = -450_000.0
    s = _make_sweeper(fft_samples=fft_n, sample_rate=fs, center_freq=fc)

    n = np.arange(fft_n)
    tone = np.exp(2j * np.pi * offset * n / fs)

    result = s.analyze_with_fft(tone)
    bin_hz = fs / fft_n
    assert result['peak_frequency_hz'] == pytest.approx(fc + offset, abs=bin_hz)


def test_fft_bins_span_center_plus_minus_half_fs():
    """freq_bins_hz must run from center-Fs/2 up to just below center+Fs/2."""
    fft_n = 1024
    fs = 2_400_000
    fc = 100e6
    s = _make_sweeper(fft_samples=fft_n, sample_rate=fs, center_freq=fc)
    result = s.analyze_with_fft(np.ones(fft_n, dtype=complex))

    bins = result['freq_bins_hz']
    assert len(bins) == fft_n
    assert bins[0] == pytest.approx(fc - fs / 2)
    assert bins[-1] == pytest.approx(fc + fs / 2 - fs / fft_n)


def test_fft_magnitude_length_matches_bins():
    fft_n = 512
    s = _make_sweeper(fft_samples=fft_n)
    result = s.analyze_with_fft(np.ones(fft_n, dtype=complex))
    assert len(result['fft_magnitude']) == len(result['freq_bins_hz']) == fft_n


def test_fft_accepts_nx2_real_pairs():
    """N x 2 [I, Q] input is coerced to complex and analyzed."""
    fft_n = 1024
    fs = 2_400_000
    fc = 100e6
    offset = 200_000.0
    s = _make_sweeper(fft_samples=fft_n, sample_rate=fs, center_freq=fc)

    n = np.arange(fft_n)
    tone = np.exp(2j * np.pi * offset * n / fs)
    iq_pairs = np.column_stack([tone.real, tone.imag])

    result = s.analyze_with_fft(iq_pairs)
    bin_hz = fs / fft_n
    assert result['peak_frequency_hz'] == pytest.approx(fc + offset, abs=bin_hz)


def test_fft_insufficient_samples_returns_error():
    s = _make_sweeper(fft_samples=2048)
    result = s.analyze_with_fft(np.ones(100, dtype=complex))
    assert 'error' in result


def test_fft_1d_too_short_returns_error():
    s = _make_sweeper(fft_samples=2048)
    result = s.analyze_with_fft(np.ones(2047, dtype=complex))
    assert 'error' in result


# --------------------------------------------------------------------------- #
# detect_peaks (unit handling + thresholding + dedup)
# --------------------------------------------------------------------------- #

def _sample(freq_hz, power_dbm):
    return SignalSample(frequency=freq_hz, sample_rate=200000, gain=35,
                        signal_power_dbm=power_dbm, timestamp=time.time())


def test_detect_peaks_empty():
    s = RtlSDRSweeper()
    assert s.detect_peaks([]) == []


def test_detect_peaks_finds_signal_above_threshold():
    """One strong bin among a flat noise floor should be detected, reported in MHz."""
    s = RtlSDRSweeper()
    samples = [_sample(100e6 + i * 100e3, -90.0) for i in range(10)]
    samples[5] = _sample(100e6 + 5 * 100e3, -70.0)  # strong peak

    peaks = s.detect_peaks(samples, threshold_db=5.0)
    assert len(peaks) == 1
    freq_mhz, power = peaks[0]
    assert freq_mhz == pytest.approx((100e6 + 5 * 100e3) / 1e6)  # returned in MHz
    assert power == pytest.approx(-70.0)


def test_detect_peaks_nothing_when_flat():
    s = RtlSDRSweeper()
    samples = [_sample(100e6 + i * 100e3, -90.0) for i in range(10)]
    assert s.detect_peaks(samples, threshold_db=5.0) == []


def test_detect_peaks_dedup_uses_hz_separation():
    """Adjacent strong bins closer than min_separation_hz collapse to one peak."""
    s = RtlSDRSweeper()
    # Two strong bins 10 kHz apart (< 50 kHz default separation) plus filler.
    samples = [
        _sample(100_000_000.0, -60.0),
        _sample(100_010_000.0, -60.0),   # 10 kHz away -> deduped
        _sample(100_500_000.0, -95.0),
        _sample(101_000_000.0, -60.0),   # far away -> separate peak
    ]
    peaks = s.detect_peaks(samples, threshold_db=5.0, min_separation_hz=50_000.0)
    assert len(peaks) == 2


def test_detect_peaks_separate_when_far_apart():
    s = RtlSDRSweeper()
    samples = [
        _sample(100_000_000.0, -60.0),
        _sample(100_200_000.0, -60.0),   # 200 kHz away -> separate
    ]
    # noise floor is -60 (both equal) so use a low threshold relative to mean.
    peaks = s.detect_peaks(samples, threshold_db=0.0, min_separation_hz=50_000.0)
    assert len(peaks) == 2


# --------------------------------------------------------------------------- #
# Hardware API wiring (device selection) - fully mocked, no real dongle
# --------------------------------------------------------------------------- #

class FakeRtlSdr:
    """Minimal stand-in matching the pyrtlsdr 0.5.0 surface we use."""
    def __init__(self, device_index=0):
        self.device_index = device_index
        self.sample_rate = None
        self.center_freq = None
        self.gain = None
        self.valid_gains_db = [0.0, 9.0, 20.0, 30.0, 40.0, 49.6]

    def set_sample_rate(self, rate):
        self.sample_rate = rate

    def set_center_freq(self, freq):
        self.center_freq = freq

    def set_gain(self, gain):
        self.gain = gain

    def read_samples(self, n):
        return np.ones(n, dtype=complex)

    def close(self):
        pass


def test_connect_passes_device_index(monkeypatch):
    """Critical for two-dongle support: device_index must reach the constructor."""
    created = {}

    def factory(device_index=0):
        created['device_index'] = device_index
        return FakeRtlSdr(device_index=device_index)

    monkeypatch.setattr(fsf, "RtlSDR", factory)

    s = RtlSDRSweeper(device_index=1)
    assert s.connect() is True
    assert created['device_index'] == 1
    # Center frequency was actually pushed to the tuner on connect.
    assert s.sdr.center_freq == int(s.center_freq)


def test_connect_clamps_gain(monkeypatch):
    monkeypatch.setattr(fsf, "RtlSDR", FakeRtlSdr)
    s = RtlSDRSweeper(device_index=0, gain=999)  # absurd gain
    assert s.connect() is True
    assert s.sdr.gain <= max(s.sdr.valid_gains_db)


def test_connect_returns_false_without_library(monkeypatch):
    """When the hardware library failed to import, connect() fails gracefully."""
    monkeypatch.setattr(fsf, "RtlSDR", None)
    s = RtlSDRSweeper()
    assert s.connect() is False
