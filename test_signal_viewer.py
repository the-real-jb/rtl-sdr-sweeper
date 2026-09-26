#!/usr/bin/env python3
"""Hardware-free tests for the signal viewer's pure logic and data shaping."""

import time

import numpy as np
import pytest

import freq_sweeper_full as fsf
from freq_sweeper_full import SignalSample
from freq_sweeper_advanced import AdvancedSweeper
import signal_viewer as sv


# --------------------------------------------------------------------------- #
# estimate_bandwidth_hz
# --------------------------------------------------------------------------- #

def test_bandwidth_of_flat_rectangle():
    """A flat plateau spanning a known width returns that width (edges inclusive)."""
    freqs = np.arange(0, 100) * 1000.0  # 1 kHz bins
    mag = np.zeros(100)
    mag[40:61] = 1.0  # bins 40..60 at full amplitude -> span 40..60 = 20 kHz
    bw = sv.estimate_bandwidth_hz(freqs, mag, db_down=6.0)
    assert bw == pytest.approx(20_000.0)


def test_bandwidth_wider_signal_is_larger():
    freqs = np.arange(0, 200) * 1000.0
    narrow = np.zeros(200); narrow[95:106] = 1.0     # ~10 kHz
    wide = np.zeros(200); wide[50:151] = 1.0          # ~100 kHz
    assert sv.estimate_bandwidth_hz(freqs, wide) > sv.estimate_bandwidth_hz(freqs, narrow)


def test_bandwidth_empty_is_zero():
    assert sv.estimate_bandwidth_hz([], []) == 0.0


def test_bandwidth_all_zero_is_zero():
    freqs = np.arange(0, 10) * 1000.0
    assert sv.estimate_bandwidth_hz(freqs, np.zeros(10)) == 0.0


def test_bandwidth_respects_db_threshold():
    """A -6 dB point sits at half amplitude; a shoulder above half must be included."""
    freqs = np.arange(0, 9) * 1000.0
    #        0    1    2    3     4(pk) 5    6    7    8
    mag = np.array([0.0, 0.1, 0.4, 0.6, 1.0, 0.6, 0.4, 0.1, 0.0])
    # 0.5 threshold (-6 dB): bins 3..5 are >= 0.5 -> span = 2 kHz
    bw = sv.estimate_bandwidth_hz(freqs, mag, db_down=6.0)
    assert bw == pytest.approx(2000.0)


# --------------------------------------------------------------------------- #
# peak_to_freq_hz
# --------------------------------------------------------------------------- #

def test_peak_to_freq_hz():
    peaks = [(88.5, -94.9), (104.0, -95.7)]
    assert sv.peak_to_freq_hz(peaks, 0) == pytest.approx(88.5e6)
    assert sv.peak_to_freq_hz(peaks, 1) == pytest.approx(104.0e6)


def test_peak_to_freq_hz_out_of_range():
    with pytest.raises(IndexError):
        sv.peak_to_freq_hz([], 0)
    with pytest.raises(IndexError):
        sv.peak_to_freq_hz([(88.5, -90.0)], 5)


# --------------------------------------------------------------------------- #
# sweep_to_plotdata
# --------------------------------------------------------------------------- #

def _sample(freq_hz, power):
    return SignalSample(frequency=freq_hz, sample_rate=200000, gain=35,
                        signal_power_dbm=power, timestamp=time.time())


def test_sweep_to_plotdata_sorted():
    samples = [_sample(104e6, -95.0), _sample(88.5e6, -94.0), _sample(100e6, -101.0)]
    data = sv.sweep_to_plotdata(samples)
    assert data['freqs_mhz'] == [88.5, 100.0, 104.0]  # ascending
    assert data['power_dbm'] == [-94.0, -101.0, -95.0]


# --------------------------------------------------------------------------- #
# fft_to_spectrum
# --------------------------------------------------------------------------- #

def test_fft_to_spectrum_peak_is_zero_db():
    fft_result = {
        'fft_magnitude': np.array([0.1, 1.0, 0.2]),
        'freq_bins_hz': np.array([99e6, 100e6, 101e6]),
        'peak_bin': 1,
        'peak_frequency_hz': 100e6,
    }
    spec = sv.fft_to_spectrum(fft_result)
    assert max(spec['power_db']) == pytest.approx(0.0)  # normalized to peak
    assert spec['peak_frequency_hz'] == pytest.approx(100e6)
    assert spec['freqs_mhz'] == [99.0, 100.0, 101.0]
    assert spec['bandwidth_hz'] >= 0.0


def test_fft_to_spectrum_passes_error_through():
    assert 'error' in sv.fft_to_spectrum({'error': 'Insufficient samples for FFT'})


# --------------------------------------------------------------------------- #
# SignalViewer.zoom with a fake dongle (real analyze_with_fft, no hardware)
# --------------------------------------------------------------------------- #

class FakeSdr:
    """Emits a clean complex tone at a fixed baseband offset (relative to tune)."""
    def __init__(self, offset_hz, sample_rate):
        self.offset_hz = offset_hz
        self.sample_rate = sample_rate
        self._n = 0

    def read_samples(self, n):
        idx = np.arange(self._n, self._n + n)
        self._n += n
        return np.exp(2j * np.pi * self.offset_hz * idx / self.sample_rate)


def test_viewer_zoom_returns_waterfall_and_bandwidth(monkeypatch):
    fs = 2_400_000
    fft_n = 1024
    baseband_offset = 100_000.0   # tone sits 100 kHz above the tuned center
    tune_hz = 100.25e6

    sweeper = AdvancedSweeper(device_index=0, sample_rate=fs, fft_samples=fft_n)
    sweeper.sdr = FakeSdr(baseband_offset, fs)     # inject fake hardware
    # set_center_frequency would call sdr.set_center_freq; FakeSdr lacks it, so stub.
    monkeypatch.setattr(sweeper, 'set_center_frequency',
                        lambda f: setattr(sweeper, 'center_freq', float(f)))

    viewer = sv.SignalViewer(sweeper=sweeper)
    result = viewer.zoom(tune_hz, frames=5)

    assert 'error' not in result
    assert len(result['waterfall']) == 5
    # each waterfall row has one value per FFT bin
    assert all(len(row) == fft_n for row in result['waterfall'])
    # peak = retuned center + the tone's baseband offset, within one bin
    bin_hz = fs / fft_n
    assert result['peak_frequency_hz'] == pytest.approx(tune_hz + baseband_offset, abs=bin_hz)
    # a single clean tone -> narrow bandwidth (only the window main lobe)
    assert 0.0 <= result['bandwidth_hz'] <= 10 * bin_hz


def test_viewer_sweep_shapes_data(monkeypatch):
    """SignalViewer.sweep wires sweep_frequency + detect_peaks into plot data."""
    fs = 2_400_000
    sweeper = AdvancedSweeper(device_index=0, sample_rate=fs, fft_samples=1024)
    sweeper.buffer_size = 256

    class ConstSdr:
        def read_samples(self, n):
            return np.full(n, 0.5, dtype=complex)
        def set_center_freq(self, f):
            pass
    sweeper.sdr = ConstSdr()
    monkeypatch.setattr(fsf.time, 'sleep', lambda *_: None)  # no real delays

    viewer = sv.SignalViewer(sweeper=sweeper)
    data = viewer.sweep(100.0, 100.4, 200.0)  # 100.0, 100.2, 100.4 MHz -> 3 steps

    assert data['freqs_mhz'] == [100.0, 100.2, 100.4]
    assert len(data['power_dbm']) == 3
    assert 'peaks' in data
