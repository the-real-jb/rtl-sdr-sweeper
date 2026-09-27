# RTL-SDR Frequency Sweeper

Scans frequencies with an RTL-SDR dongle and reports detected signals. Power
estimates, peak picking, the FFT, and bandwidth math run without hardware and
are covered by pytest. Sample capture, the dwell between buffers, and dongle
I/O need a real device and `librtlsdr`.

- `freq_sweeper_full.py` — canonical base engine (`RtlSDRSweeper`, `SignalSample`, `FrequencyBand`, `frequency_steps`).
- `freq_sweeper_advanced.py` — `AdvancedSweeper` subclass (windowed FFT, one-shot band sweep, pyqtgraph spectrum of the current center).
- `freq_sweeper.py` — compatibility shim that re-exports the above and starts the pyqtgraph view.
- `signal_viewer.py` + `viewer.html` — local HTTP viewer: wideband sweep, peak list, click-to-zoom FFT waterfall.
- `radio_service.py`, `radio.py`, `run_radio.sh` — localhost radio backend (API on `127.0.0.1:8766`) and CLI client (`scan`, `listen`, `play`). The CLI is HTTP-only and never opens a dongle.
- `test_freq_sweeper.py`, `test_signal_viewer.py` — pytest for the pure DSP/logic (no hardware).

## What it does today

`RtlSDRSweeper` opens one dongle by index, sets sample rate and gain (a dB
value or `'auto'`, clamped to the tuner's supported gains), then steps a band.

- `frequency_steps(start_hz, stop_hz, step_hz)` starts at `start_hz` and
  includes `stop_hz` only when that frequency lands on a step. 88–89 MHz at
  500 kHz includes 89.0 MHz. 87.5–88.0 MHz at 200 kHz stops at 87.9 MHz, the
  same rule as the built-in FM sweep (`87.5–108 MHz` at 200 kHz in
  `freq_sweeper_full.main`). The advanced CLI and the viewer default to
  `88–108 MHz` at 500 kHz.
- Each step reads a few IQ buffers, waits 50 ms between reads, and stores a
  `SignalSample` (frequency, rate, gain, relative power, timestamp).
- `get_signal_power` maps mean `|IQ|^2` onto a relative dBm scale offset by
  `NOISE_FLOOR_DBM` (−95). The RTL-SDR is not an absolute power meter; use
  the numbers to compare frequencies, not to quote calibrated field strength.
- `detect_peaks` keeps samples at least 5 dB above that sweep's average power,
  and drops a hit closer than 50 kHz to the previous one. Both thresholds are
  arguments.
- `analyze_spectrum` prints the scanned range, noise floor, extrema, and peaks.
- `FrequencyBand` names reference centers (FM 87.5, air 118, 2 m, 70 cm, UHF
  TV). The enum is not applied to detections.

`AdvancedSweeper.analyze_with_fft` takes 2048 complex samples, or an N×2 array
of real I/Q pairs. A Hann window (or a rectangular window) is divided by its
mean so a pure tone's magnitude stays comparable, then the bins are mapped to
`center ± sample_rate/2` and the peak bin is reported as an RF frequency.
`--sweep` retunes across the requested band and prints the spectrum report.
Without `--sweep`, pyqtgraph plots a live spectrum at the connected center
frequency (default 100 MHz).

`signal_viewer.py` serves `viewer.html` on `127.0.0.1:8765`. Requests are
handled one at a time so librtlsdr stays on the thread that opened the device.

- `GET /api/sweep?start=&stop=&step=` returns one averaged power point per
  tuned frequency plus detected peaks. The page stacks the last 40 sweeps
  into a wideband heatmap.
- `GET /api/zoom?freq_hz=&frames=` (default 20 frames) retunes to that
  frequency and returns a spectrum in dB relative to the peak, a short FFT
  waterfall, and the contiguous −6 dB bandwidth around the peak. The page
  labels that width as wide FM, a wide data-like signal, narrow voice, or a
  narrow carrier. The label comes from bandwidth alone.

One dongle is open at a time. Relative power is the measurement throughout.

## Radio backend

`./run_radio.sh` starts the API on http://127.0.0.1:8766 (default device 0). Subcommands talk to that service:

```bash
./run_radio.sh scan --band fm              # 88–108 MHz interesting hits
./run_radio.sh scan                        # default band activity (118–174 MHz)
./run_radio.sh listen --freq 88.5 --mode wfm
./run_radio.sh play --band fm              # scan, pick a hit number, listen
```

Named bands: `fm` (88–108 MHz), `air` (118–137), `ham2m` (144–148), `ham70cm` (420–450), `activity` (118–174, default when `scan` has no `--band` or `--start`).

Hits are ranked by reason — `hunt` (watch frequencies), then `narrow_in_wide` (narrow carriers in FM broadcast), `voice` (air/marine/ham), then `active` — strongest relative dBm first within each group. Palmetto 800 (`769–775` / `851–861` MHz) returns HTTP 409; the CLI prints the SDRTrunk launcher path and exits 2.

If the dongle is busy (`LIBUSB_ERROR_BUSY`), the CLI prints the error string from `GET /api/health` and exits non-zero.

Routes on that port: `GET /api/health`, `GET /api/sweep`, `GET /api/zoom?freq_hz=`, `POST /api/listen`, `POST /api/listen/stop`, and `GET /api/audio` (48 kHz mono s16le). `listen` prints the −6 dB width returned by `/api/zoom`. Demod uses SciPy.

## Running

Use the wrapper script (recommended):

```bash
./run_sweeper.sh --sweep --device 1                 # 88-108 MHz at 500 kHz on dongle 1
./run_sweeper.sh --sweep --device 0 --start 118 --stop 137 --step 25   # airband on dongle 0
./run_sweeper.sh --device 1                         # live spectrum at the current center (needs pyqtgraph)
./run_viewer.sh --device 1                          # browser viewer, http://127.0.0.1:8765
./run_viewer.sh --device 0 --port 8080
```

`--device 0` / `--device 1` selects which physical dongle to use. `--gain`
accepts a dB number or `auto`. The viewer also takes `--sample-rate` (default
2.4 MSPS). `freq_sweeper_full.py` with no arguments sweeps FM broadcast
`87.5–108 MHz` at 200 kHz on device 0.

## Important: librtlsdr on this machine

The **system** `librtlsdr` (`/lib/x86_64-linux-gnu/librtlsdr.so.2`) is too old — it
does not export `rtlsdr_set_dithering`, which pyrtlsdr 0.5.0 requires at import time.

A newer librtlsdr (the `librtlsdr/librtlsdr` fork, which has that symbol) was built
and installed into `~/.local` with **no root**:

```bash
git clone https://github.com/librtlsdr/librtlsdr.git
cd librtlsdr && mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=$HOME/.local -DINSTALL_UDEV_RULES=OFF -DDETACH_KERNEL_DRIVER=ON
make -j$(nproc) && make install
```

pyrtlsdr finds it via `LD_LIBRARY_PATH`, which `run_sweeper.sh` and `run_viewer.sh` set automatically:

```bash
export LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH"
```

## Two dongles

Both dongles (Realtek RTL2838, R820T/2 tuner) are present:

- index 0 — serial `00000978`
- index 1 — serial `00001090`

If a dongle reports `LIBUSB_ERROR_BUSY`, another program is using it. On this host the
`dump1090-mutability` ADS-B service holds one dongle. To free it (needs root):

```bash
sudo systemctl stop dump1090-mutability
```

## Tests

```bash
.venv/bin/python -m pytest
```

The suite fakes `RtlSdr` where a device object is required. It checks the
frequency grid, relative-power math, FFT bin mapping, peak separation,
bandwidth, and the viewer's plot shaping. Hardware capture, the 50 ms dwell,
and dongle I/O stay untested.

## Future enhancements

- **Max-hold persistence.** The browser heatmap keeps the last 40 full sweeps, and the pyqtgraph window is one live FFT. A max-hold or decaying trace would keep a short transmission visible after the sweep has moved on.
- **Name detected signals.** `FrequencyBand` and the viewer's width guess do not identify a hit. Mapping frequency plus the −6 dB width onto FM channels, airband, weather, or ham allocations turns a list of MHz into a station you can recognize.
- **Scan presets.** Start, stop, and step are flags and form fields. Named presets for the grids already used here (FM `87.5–108 MHz` at 200 kHz, the CLI default `88–108 MHz` at 500 kHz, air `118–137 MHz` at 25 kHz, and the other `FrequencyBand` ranges) would make those scans one selection.
- **Log peaks to disk.** `detect_peaks` and the viewer's `last_peaks` exist only for the current run. Appending timestamp, frequency, and relative dBm lets an unattended scan be reviewed afterward.
- **Sweep and zoom on separate dongles.** `--device` opens one index. This host has two RTL2838 dongles; holding one on a zoom frequency while the other continues the wide sweep would keep the band scan running during a focus.
- **Alert when a peak appears.** Detection already uses a dB threshold over the sweep average. Comparing each sweep with the previous one and notifying on a new or stronger peak is what makes a long-running scan useful.
