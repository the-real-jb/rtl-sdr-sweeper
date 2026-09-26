#!/usr/bin/env python3
"""Browser-based spectrum / waterfall viewer for the RTL-SDR sweeper.

A small stdlib HTTP server exposes JSON endpoints that drive a Plotly.js UI
(``viewer.html``). It reuses ``AdvancedSweeper`` for all capture/FFT work:

- ``/api/sweep``  runs a wideband band sweep -> power-vs-frequency + detected peaks.
- ``/api/zoom``   retunes the dongle to a peak and returns a narrowband spectrum,
                  a stacked waterfall (time x frequency) and a bandwidth estimate.

The pure data-shaping / DSP helpers at the top are hardware-free and unit-tested.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np

from freq_sweeper_advanced import AdvancedSweeper


# --------------------------------------------------------------------------- #
# Pure logic (no hardware) - unit tested
# --------------------------------------------------------------------------- #

def estimate_bandwidth_hz(freq_bins_hz, magnitude, db_down: float = 6.0) -> float:
    """Estimate occupied bandwidth as the contiguous span around the peak that
    stays within ``db_down`` dB of the peak amplitude.

    Args:
        freq_bins_hz: Frequency of each bin in Hz (monotonic).
        magnitude: Linear magnitude per bin (same length as freq_bins_hz).
        db_down: How far below the peak (in dB) defines the band edges (default 6 dB).

    Returns:
        Bandwidth in Hz (0.0 if input is empty/degenerate).
    """
    freq = np.asarray(freq_bins_hz, dtype=float)
    mag = np.asarray(magnitude, dtype=float)
    if freq.size == 0 or mag.size == 0 or freq.size != mag.size:
        return 0.0

    peak = int(np.argmax(mag))
    peak_mag = mag[peak]
    if peak_mag <= 0:
        return 0.0

    threshold = peak_mag * (10.0 ** (-db_down / 20.0))  # amplitude ratio

    lo = peak
    while lo - 1 >= 0 and mag[lo - 1] >= threshold:
        lo -= 1
    hi = peak
    while hi + 1 < mag.size and mag[hi + 1] >= threshold:
        hi += 1

    return abs(float(freq[hi]) - float(freq[lo]))


def peak_to_freq_hz(peaks, index: int) -> float:
    """Map a detected-peak list entry to a tuning frequency in Hz.

    ``peaks`` is the output of ``detect_peaks``: a list of (freq_mhz, power_dbm).
    """
    if not peaks or index < 0 or index >= len(peaks):
        raise IndexError(f"peak index {index} out of range (have {len(peaks)})")
    return float(peaks[index][0]) * 1e6


def sweep_to_plotdata(samples_list) -> dict:
    """Shape a list of SignalSample into sorted arrays for a power-vs-frequency plot.

    A sweep records several buffers per frequency step; those are averaged so each
    frequency appears once (a clean spectrum trace).
    """
    from collections import defaultdict
    acc = defaultdict(list)
    for s in samples_list:
        acc[float(s.frequency)].append(float(s.signal_power_dbm))

    freqs = sorted(acc)
    return {
        'freqs_mhz': [f / 1e6 for f in freqs],
        'power_dbm': [float(np.mean(acc[f])) for f in freqs],
    }


def fft_to_spectrum(fft_result: dict, db_down: float = 6.0) -> dict:
    """Convert an ``analyze_with_fft`` result into a normalized-dB spectrum plus
    peak frequency and estimated bandwidth."""
    if 'error' in fft_result:
        return {'error': fft_result['error']}

    mag = np.asarray(fft_result['fft_magnitude'], dtype=float)
    freqs = np.asarray(fft_result['freq_bins_hz'], dtype=float)

    peak_mag = float(mag.max()) if mag.size else 0.0
    ref = peak_mag if peak_mag > 0 else 1.0
    power_db = 20.0 * np.log10(np.maximum(mag, 1e-12) / ref)

    return {
        'freqs_mhz': (freqs / 1e6).tolist(),
        'power_db': power_db.tolist(),
        'peak_frequency_hz': float(fft_result['peak_frequency_hz']),
        'bandwidth_hz': estimate_bandwidth_hz(freqs, mag, db_down=db_down),
    }


# --------------------------------------------------------------------------- #
# Viewer engine (wraps AdvancedSweeper; hardware only touched in sweep/zoom)
# --------------------------------------------------------------------------- #

class SignalViewer:
    """Runs sweeps and zoom captures through an AdvancedSweeper."""

    def __init__(self, device_index: int = 0, gain=40, sample_rate: int = 2400000,
                 fft_samples: int = 2048, sweeper: AdvancedSweeper = None):
        # ``sweeper`` may be injected for testing without hardware.
        self.sweeper = sweeper or AdvancedSweeper(
            device_index=device_index, gain=gain,
            sample_rate=sample_rate, fft_samples=fft_samples)
        self._lock = threading.Lock()
        self.last_peaks = []

    def connect(self) -> bool:
        return self.sweeper.connect()

    def close(self) -> None:
        self.sweeper.disconnect()

    def sweep(self, start_mhz: float, stop_mhz: float, step_khz: float) -> dict:
        """Run one wideband sweep; return plot data + detected peaks."""
        with self._lock:
            samples = self.sweeper.sweep_frequency(
                start_freq=start_mhz * 1e6,
                stop_freq=stop_mhz * 1e6,
                step_size=step_khz * 1e3,
                num_buffers_per_step=2,
            )
            self.last_peaks = self.sweeper.detect_peaks(samples)

        data = sweep_to_plotdata(samples)
        data['peaks'] = [{'freq_mhz': f, 'power_dbm': p} for f, p in self.last_peaks]
        return data

    def zoom(self, freq_hz: float, frames: int = 20) -> dict:
        """Retune to ``freq_hz`` and capture a narrowband spectrum + waterfall."""
        with self._lock:
            self.sweeper.set_center_frequency(freq_hz)
            n = self.sweeper.fft_samples
            waterfall = []
            last_spectrum = None
            for _ in range(frames):
                samples = np.asarray(self.sweeper.sdr.read_samples(n))
                result = self.sweeper.analyze_with_fft(samples)
                spectrum = fft_to_spectrum(result)
                if 'error' in spectrum:
                    continue
                waterfall.append(spectrum['power_db'])
                last_spectrum = spectrum

        if last_spectrum is None:
            return {'error': 'no valid frames captured'}

        last_spectrum['waterfall'] = waterfall
        last_spectrum['center_freq_hz'] = float(freq_hz)
        return last_spectrum


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

_HTML_PATH = Path(__file__).with_name('viewer.html')


def make_handler(viewer: SignalViewer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            try:
                if parsed.path in ('/', '/index.html'):
                    body = _HTML_PATH.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == '/api/sweep':
                    start = float(qs.get('start', ['88'])[0])
                    stop = float(qs.get('stop', ['108'])[0])
                    step = float(qs.get('step', ['500'])[0])
                    self._send_json(viewer.sweep(start, stop, step))
                elif parsed.path == '/api/zoom':
                    freq_hz = float(qs.get('freq_hz', ['0'])[0])
                    frames = int(qs.get('frames', ['20'])[0])
                    self._send_json(viewer.zoom(freq_hz, frames=frames))
                else:
                    self._send_json({'error': 'not found'}, code=404)
            except Exception as e:  # pragma: no cover - defensive
                self._send_json({'error': str(e)}, code=500)

    return Handler


def serve(device_index: int = 0, host: str = '127.0.0.1', port: int = 8765,
          gain=40, sample_rate: int = 2400000):
    viewer = SignalViewer(device_index=device_index, gain=gain, sample_rate=sample_rate)
    if not viewer.connect():
        print("Could not connect to the RTL-SDR; is the dongle free and librtlsdr set up?")
        return

    # Single-threaded: libusb/librtlsdr must be driven from the thread that opened
    # the device, so we deliberately serialize all requests here.
    server = HTTPServer((host, port), make_handler(viewer))
    print(f"Signal viewer running at http://{host}:{port}  (device {device_index})")
    print("Open that URL in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping viewer...")
    finally:
        server.shutdown()
        viewer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RTL-SDR web spectrum/waterfall viewer")
    parser.add_argument("--device", type=int, default=0, help="RTL-SDR device index (0 or 1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port")
    parser.add_argument("--gain", default=40, help="Tuner gain in dB or 'auto'")
    parser.add_argument("--sample-rate", type=int, default=2400000, help="Sample rate (Hz)")
    args = parser.parse_args()
    gain = args.gain if args.gain == 'auto' else float(args.gain)
    serve(device_index=args.device, port=args.port, gain=gain, sample_rate=args.sample_rate)
