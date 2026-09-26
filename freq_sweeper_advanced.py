#!/usr/bin/env python3
"""Enhanced RTL-SDR Sweeper with FFT Spectrum Analysis.

Canonical subclass module. Extends the ``RtlSDRSweeper`` base class from
``freq_sweeper_full`` with an FFT analyzer and an optional real-time
visualization loop.
"""

import time

import numpy as np

from freq_sweeper_full import RtlSDRSweeper


class AdvancedSweeper(RtlSDRSweeper):
    """Advanced version with FFT and detailed signal analysis."""

    def __init__(self, *args, fft_samples: int = 2048, window_type: str = 'hann', **kwargs):
        super().__init__(*args, **kwargs)
        self.fft_samples = fft_samples
        self.window_type = window_type

    def _to_complex(self, samples_array: np.ndarray) -> np.ndarray:
        """Coerce input into a 1-D complex array.

        Accepts either a 1-D complex IQ array or an N x 2 real array of [I, Q] pairs.
        """
        samples_array = np.asarray(samples_array)
        if samples_array.ndim == 2 and samples_array.shape[1] == 2:
            return samples_array[:, 0] + 1j * samples_array[:, 1]
        return samples_array.astype(np.complex128)

    def analyze_with_fft(self, samples_array: np.ndarray) -> dict:
        """
        Perform FFT analysis on complex I/Q samples.

        Args:
            samples_array: Complex I/Q samples (1-D complex) or an N x 2 array
                of real [I, Q] pairs.

        Returns:
            Dict with the magnitude spectrum, the frequency of each bin in Hz
            (mapped to center_freq +/- sample_rate/2), the peak bin index and the
            actual peak frequency in Hz.
        """
        complex_data = self._to_complex(samples_array)

        n = self.fft_samples
        if complex_data.ndim != 1 or complex_data.shape[0] < n:
            return {'error': 'Insufficient samples for FFT'}

        # Windowing. Dividing by the window's coherent gain (its mean) keeps the
        # magnitude of a pure tone calibrated regardless of the window used.
        if self.window_type == 'hann':
            window = np.hanning(n)
        else:
            window = np.ones(n)
        coherent_gain = float(np.mean(window))

        windowed = complex_data[:n] * window

        # Complex IQ -> full FFT + fftshift so bins run from -Fs/2 .. +Fs/2.
        spectrum = np.fft.fftshift(np.fft.fft(windowed))
        magnitude = np.abs(spectrum) / (n * coherent_gain)

        # fftfreq gives baseband offsets; shift and add center_freq to get RF Hz.
        freq_offsets = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / self.sample_rate))
        freq_bins_hz = freq_offsets + self.center_freq

        peak_bin = int(np.argmax(magnitude))

        return {
            'fft_magnitude': magnitude,
            'freq_bins_hz': freq_bins_hz,
            'peak_bin': peak_bin,
            'peak_frequency_hz': float(freq_bins_hz[peak_bin]),
        }


def interactive_sweep(device_index: int = 0):
    """Interactive sweep with optional real-time visualization.

    Requires pyqtgraph for plotting; if it is not installed a clear message is
    printed and the function returns.
    """
    try:
        from pyqtgraph.Qt import QtWidgets  # noqa: F401
        import pyqtgraph as pg
    except ImportError:
        print("pyqtgraph not installed; skipping interactive visualization.")
        print("Install it with: uv pip install pyqtgraph pyqt5")
        return

    app = pg.mkQApp("RTL-SDR Spectrum")
    plot_widget = pg.PlotWidget(title="Real-Time Spectrum")
    plot_widget.setLabel('left', 'Magnitude')
    plot_widget.setLabel('bottom', 'Frequency', units='Hz')
    plot_widget.showGrid(x=True, y=True)
    curve = plot_widget.plot(pen='y')
    plot_widget.show()

    print("Starting interactive sweep... (close the window or Ctrl+C to stop)")

    sweeper = AdvancedSweeper(device_index=device_index, gain=35, sample_rate=2400000)
    if not sweeper.connect():
        return

    try:
        while True:
            samples = sweeper.sdr.read_samples(sweeper.fft_samples)
            samples = np.asarray(samples)
            if samples.size >= sweeper.fft_samples:
                result = sweeper.analyze_with_fft(samples)
                if 'error' not in result:
                    power_dbm = sweeper.get_signal_power(samples)
                    freq_mhz = sweeper.center_freq / 1e6
                    curve.setData(result['freq_bins_hz'], result['fft_magnitude'])
                    plot_widget.setTitle(
                        f"Current: {freq_mhz:.2f} MHz | Power: {power_dbm:.1f} dBm")
            app.processEvents()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nInteractive mode stopped.")
    finally:
        sweeper.disconnect()


def band_sweep(device_index: int = 0,
               start_mhz: float = 88.0,
               stop_mhz: float = 108.0,
               step_khz: float = 500.0,
               gain=40,
               sample_rate: int = 2400000):
    """Run a single bounded band sweep and print the analysis report, then exit."""
    sweeper = AdvancedSweeper(device_index=device_index, gain=gain, sample_rate=sample_rate)
    sweeper.buffer_size = 16384
    if not sweeper.connect():
        return
    try:
        samples = sweeper.sweep_frequency(
            start_freq=start_mhz * 1e6,
            stop_freq=stop_mhz * 1e6,
            step_size=step_khz * 1e3,
            num_buffers_per_step=2,
        )
        sweeper.analyze_spectrum(samples)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sweeper.disconnect()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Advanced RTL-SDR sweeper")
    parser.add_argument("--device", type=int, default=0,
                        help="RTL-SDR device index (0 or 1)")
    parser.add_argument("--sweep", action="store_true",
                        help="Run a single bounded band sweep (text report) and exit")
    parser.add_argument("--start", type=float, default=88.0, help="Start freq in MHz")
    parser.add_argument("--stop", type=float, default=108.0, help="Stop freq in MHz")
    parser.add_argument("--step", type=float, default=500.0, help="Step size in kHz")
    parser.add_argument("--gain", default=40, help="Tuner gain in dB or 'auto'")
    args = parser.parse_args()

    gain = args.gain if args.gain == 'auto' else float(args.gain)

    if args.sweep:
        band_sweep(args.device, args.start, args.stop, args.step, gain=gain)
    else:
        interactive_sweep(args.device)
