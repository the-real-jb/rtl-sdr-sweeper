#!/usr/bin/env python3
"""
RTL-SDR Frequency Sweeper - Detects nearby radio transmissions by scanning frequencies.

Canonical base module. Defines the ``RtlSDRSweeper`` engine, the ``SignalSample``
dataclass and the ``FrequencyBand`` reference enum. Uses the pyrtlsdr (``rtlsdr``)
library for the hardware interface.

Hardware note: importing/instantiating the real RTL-SDR library requires a
librtlsdr.so that exports ``rtlsdr_set_dithering``. If the system library is too
old, the import below fails and ``connect()`` returns a clear error instead of
crashing. The pure DSP/logic helpers (``get_signal_power``, ``detect_peaks``, etc.)
do not need hardware and remain fully usable/testable.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple

import numpy as np

# Import the hardware class per the pyrtlsdr 0.5.0 API. The package module is
# ``rtlsdr`` and the class is ``RtlSdr`` (aliased to RtlSDR for readability).
# Guard the import so this module can still be imported (for the pure DSP helpers
# and for tests) on machines whose librtlsdr is too old.
try:
    from rtlsdr import RtlSdr as RtlSDR
    RTLSDR_IMPORT_ERROR = None
except Exception as _import_err:  # pragma: no cover - depends on system library
    RtlSDR = None
    RTLSDR_IMPORT_ERROR = _import_err


@dataclass
class SignalSample:
    frequency: float          # Center frequency in Hz
    sample_rate: int          # Samples per second
    gain: float               # Tuner gain in dB (or 'auto')
    signal_power_dbm: float   # Estimated signal strength (rough dBm estimate)
    timestamp: float          # Time in seconds (epoch)


class FrequencyBand(Enum):
    """Common radio bands for reference (center-ish frequencies in Hz)."""
    FM_BROADCAST = 87.5e6   # 87.5 - 108 MHz
    AIR_BAND = 118e6        # Air band aircraft comms
    HAM_2M = 144e6          # 2m Ham radio
    HAM_70CM = 430e6        # 70cm Ham radio
    TV_UHF = 470e6          # UHF TV


def frequency_steps(start_freq: float, stop_freq: float, step_size: float) -> List[float]:
    """Center frequencies a sweep tunes, from start through the last step that is not past stop."""
    current = float(start_freq)
    stop = float(stop_freq)
    step = float(step_size)
    steps: List[float] = []
    while current <= stop:
        steps.append(current)
        current += step
    return steps


class RtlSDRSweeper:
    """RTL-SDR Frequency Sweeper with signal detection and analysis."""

    # Rough calibration constant. The RTL-SDR is not an absolute-power meter;
    # this maps normalized IQ power (samples in [-1, 1]) to an approximate dBm
    # scale so relative comparisons across frequencies are meaningful.
    NOISE_FLOOR_DBM = -95.0

    def __init__(self, device_index: int = 0, gain=35, sample_rate: int = 200000):
        """
        Initialize the RTL-SDR sweeper.

        Args:
            device_index: Which USB RTL-SDR to use (0 for the first device,
                1 for the second, etc.).
            gain: Tuner gain in dB (typ. 0-49.6) or the string 'auto' for AGC.
                The driver rounds to the nearest gain the tuner supports.
            sample_rate: Sample rate in samples/second (~230k-3.2M typical).
        """
        self.device_index = device_index
        self.gain = gain
        self.sample_rate = sample_rate

        self.sdr = None
        # Samples per buffer read. Must keep (2 * buffer_size) a multiple of 512
        # bytes or librtlsdr raises LIBUSB_ERROR_OVERFLOW; 16384 (=32768 bytes) is
        # a safe, fast power-of-two default.
        self.buffer_size = 16384
        self.center_freq = 100e6   # Default center frequency (100 MHz)

    def connect(self) -> bool:
        """Connect to the RTL-SDR device and configure it. Returns True on success."""
        if RtlSDR is None:
            print("RTL-SDR library unavailable: "
                  f"{RTLSDR_IMPORT_ERROR}")
            print("This usually means the system librtlsdr is too old/missing "
                  "(e.g. it does not export rtlsdr_set_dithering). Install a "
                  "current librtlsdr and reconnect the dongle.")
            return False

        print(f"Connecting to RTL-SDR device {self.device_index}...")
        try:
            # device_index selects which dongle (0, 1, ...). This is required
            # to address the second dongle.
            self.sdr = RtlSDR(device_index=self.device_index)

            self.sdr.set_sample_rate(self.sample_rate)
            self._apply_gain(self.gain)
            # Make sure the tuner actually has a center frequency before reads.
            self.sdr.set_center_freq(int(self.center_freq))

            print("Connected successfully!")
            print("Device Info:")
            print(f"  Device index: {self.device_index}")
            print(f"  Sample rate:  {self.sample_rate / 1e6:.3f} MSPS")
            print(f"  Center freq:  {self.center_freq / 1e6:.3f} MHz")
            print(f"  Gain:         {self.gain}")
        except Exception as e:
            print(f"Error connecting to device: {e}")
            print("Make sure your RTL-SDR is connected and the driver is loaded.")
            self.sdr = None
            return False

        return True

    def _apply_gain(self, gain) -> None:
        """Set tuner gain, clamping numeric gains to the device's supported range."""
        if gain == 'auto' or gain is None:
            self.sdr.set_gain('auto')
            return

        gain = float(gain)
        valid = getattr(self.sdr, 'valid_gains_db', None)
        if valid:
            # Clamp into the supported range; the driver snaps to the nearest gain.
            gain = min(max(gain, min(valid)), max(valid))
        self.sdr.set_gain(gain)
        self.gain = gain

    def disconnect(self) -> None:
        """Close the connection."""
        if self.sdr:
            try:
                self.sdr.close()
                print("Disconnected from device.")
            except Exception as e:
                print(f"Error during disconnect: {e}")
            finally:
                self.sdr = None

    def set_center_frequency(self, frequency_hz) -> None:
        """Set the center frequency for reading (Hz)."""
        self.center_freq = float(frequency_hz)
        if self.sdr is not None:
            try:
                self.sdr.set_center_freq(int(self.center_freq))
                print(f"Center frequency set to {self.center_freq / 1e6:.3f} MHz")
            except Exception as e:
                print(f"Error setting center frequency: {e}")

    def get_signal_power(self, samples: np.ndarray) -> float:
        """
        Estimate signal power (rough dBm) from complex I/Q samples.

        This is a relative estimate, not an absolute calibrated measurement:
        pyrtlsdr returns IQ normalized to [-1, 1], and NOISE_FLOOR_DBM offsets
        the result onto an approximate dBm scale.
        """
        samples = np.asarray(samples)
        if samples.size == 0:
            # Nothing to measure; report the assumed floor rather than -inf/NaN.
            return self.NOISE_FLOOR_DBM

        avg_power = float(np.mean(np.abs(samples) ** 2))
        if avg_power <= 0.0:
            return self.NOISE_FLOOR_DBM

        return self.NOISE_FLOOR_DBM + 10.0 * np.log10(avg_power)

    def read_samples(self, num_buffers: int = 2) -> Tuple[np.ndarray, List[SignalSample]]:
        """
        Read IQ samples and collect per-buffer power measurements.

        Returns:
            Tuple of (concatenated complex IQ samples, list of SignalSample).
        """
        all_samples = []
        samples_list = []

        try:
            for _ in range(num_buffers):
                # pyrtlsdr: read_samples(n) -> complex numpy array (normalized).
                samples = self.sdr.read_samples(self.buffer_size)

                if len(samples) > 0:
                    samples = np.asarray(samples)
                    all_samples.append(samples)

                    power_dbm = self.get_signal_power(samples)
                    samples_list.append(SignalSample(
                        frequency=self.center_freq,
                        sample_rate=self.sample_rate,
                        gain=self.gain,
                        signal_power_dbm=power_dbm,
                        timestamp=time.time(),
                    ))

                time.sleep(0.05)  # Small settle delay between reads
        except Exception as e:
            print(f"Error during sample reading: {e}")

        combined = np.concatenate(all_samples) if all_samples else np.array([], dtype=complex)
        return combined, samples_list

    def sweep_frequency(self,
                        start_freq: float,
                        stop_freq: float,
                        step_size: float = 100000.0,
                        num_buffers_per_step: int = 3) -> List[SignalSample]:
        """
        Perform a full frequency sweep across a range.

        Args:
            start_freq: Starting frequency in Hz (e.g. 87.5e6).
            stop_freq: Ending frequency in Hz.
            step_size: Frequency step size between readings (default 100 kHz).
            num_buffers_per_step: Buffers to read at each frequency.

        Returns:
            List of SignalSample objects from the entire sweep.
        """
        print("\nStarting Frequency Sweep...")
        print(f"  Start:     {start_freq / 1e6:.2f} MHz")
        print(f"  Stop:      {stop_freq / 1e6:.2f} MHz")
        print(f"  Step Size: {step_size / 1000.0:.1f} kHz")

        samples_list = []

        for current_freq in frequency_steps(start_freq, stop_freq, step_size):
            self.set_center_frequency(current_freq)

            _, freq_measurements = self.read_samples(num_buffers_per_step)
            if freq_measurements:
                samples_list.extend(freq_measurements)
                avg_power_dbm = np.mean([s.signal_power_dbm for s in freq_measurements])
                print(f"Freq: {current_freq / 1e6:.2f} MHz | Power: {avg_power_dbm:.2f} dBm")

        return samples_list

    def detect_peaks(self,
                     samples_list: List[SignalSample],
                     threshold_db: float = 5.0,
                     min_separation_hz: float = 50000.0) -> List[Tuple[float, float]]:
        """
        Detect signal peaks above a given dB threshold over the noise floor.

        Args:
            samples_list: All signal measurements from a sweep.
            threshold_db: Minimum power above the average noise floor (default 5 dB).
            min_separation_hz: Minimum spacing between reported peaks (default 50 kHz).

        Returns:
            List of (frequency_mhz, power_dbm) tuples for detected signals.
        """
        if not samples_list:
            return []

        powers = [s.signal_power_dbm for s in samples_list]
        avg_noise = float(np.mean(powers))

        peak_signals = []
        prev_freq_hz = None  # Track in Hz to match sample.frequency units.

        for sample in samples_list:
            if sample.signal_power_dbm >= (avg_noise + threshold_db):
                freq_hz = sample.frequency
                # Deduplicate detections that are closer than min_separation_hz.
                if prev_freq_hz is None or abs(freq_hz - prev_freq_hz) > min_separation_hz:
                    peak_signals.append((freq_hz / 1e6, round(float(sample.signal_power_dbm), 2)))
                    prev_freq_hz = freq_hz

        return peak_signals

    def analyze_spectrum(self, samples_list: List[SignalSample]) -> dict:
        """Analyze the sweep results and print a detailed report."""
        if not samples_list:
            print("No signal samples found!")
            return {}

        powers = [s.signal_power_dbm for s in samples_list]
        freqs = [s.frequency / 1e6 for s in samples_list]

        results = {
            'total_frequencies_scanned': len(samples_list),
            'start_freq_mhz': min(freqs),
            'end_freq_mhz': max(freqs),
            'avg_noise_floor_dbm': float(np.mean(powers)),
            'max_signal_dbm': max(powers),
            'min_signal_dbm': min(powers),
            'std_deviation_dbm': float(np.std(powers)),
        }

        peak_signals = self.detect_peaks(samples_list, threshold_db=5.0)
        results['detected_signals'] = peak_signals

        print("\n" + "=" * 60)
        print("SPECTRUM ANALYSIS REPORT")
        print("=" * 60)
        print(f"\nFrequency Range Scanned: {results['start_freq_mhz']:.2f} - "
              f"{results['end_freq_mhz']:.2f} MHz")
        print(f"Total Frequencies Checked: {results['total_frequencies_scanned']}")
        print("\nSignal Statistics:")
        print(f"  Average Noise Floor: {results['avg_noise_floor_dbm']:.2f} dBm")
        print(f"  Max Signal Strength: {results['max_signal_dbm']:.2f} dBm")
        print(f"  Min Signal Strength: {results['min_signal_dbm']:.2f} dBm")

        if peak_signals:
            print("\nDETECTED SIGNALS:")
            for freq, power in sorted(peak_signals, key=lambda x: -x[1]):
                print(f"  {freq:.2f} MHz | Power: {power} dBm")
            results['top_5_signals'] = peak_signals[:5]
        else:
            print("\nNo significant signals detected above noise floor.")

        return results


def main():
    """Main execution function: sweep the FM broadcast band on device 0."""
    device_index = 0
    gain = 35
    sample_rate = 2400000  # 2.4 MSPS: full FM band per step

    start_freq = 87.5e6   # FM band
    stop_freq = 108e6
    step_size = 200000.0  # 200 kHz steps

    print("=" * 60)
    print("RTL-SDR FREQUENCY SWEEPER - DETECT NEARBY RADIOS")
    print("=" * 60)

    sweeper = RtlSDRSweeper(device_index=device_index, gain=gain, sample_rate=sample_rate)
    if not sweeper.connect():
        return

    try:
        samples_list = sweeper.sweep_frequency(
            start_freq=start_freq,
            stop_freq=stop_freq,
            step_size=step_size,
            num_buffers_per_step=3,
        )
        sweeper.analyze_spectrum(samples_list=samples_list)
    except KeyboardInterrupt:
        print("\nSweep interrupted by user.")
    except Exception as e:
        print(f"Error during sweep: {e}")
    finally:
        sweeper.disconnect()


if __name__ == "__main__":
    main()
