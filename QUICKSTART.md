# Quickstart

Text sweeps, the browser viewer, and the radio backend. Wrapper scripts export
`LD_LIBRARY_PATH=$HOME/.local/lib` so pyrtlsdr loads the newer `librtlsdr`
instead of the system library. Run them from this directory.

## Radio backend

API base URL: http://127.0.0.1:8766

```bash
./run_radio.sh                              # serve the API (device 0)
./run_radio.sh scan --band fm               # 88–108 MHz hits
./run_radio.sh scan                         # activity band (118–174 MHz)
./run_radio.sh listen --freq 88.5 --mode wfm
./run_radio.sh play --band fm --device 1    # manual check: hear 88.50 MHz FM
```

Interesting hits are sorted by reason: `hunt` (watch list), `narrow_in_wide` (narrow signal in FM broadcast), `voice` (air/marine/ham), then `active`, strongest first within each group. Palmetto 800 listen attempts print the SDRTrunk path and exit 2.

Named bands: `fm`, `air`, `ham2m`, `ham70cm`, `activity` (default when `scan` has no `--band` or `--start`).

## Sweep a band

```bash
./run_sweeper.sh --sweep --device 1
```

Prints a spectrum report for 88–108 MHz at 500 kHz steps on dongle 1, then exits.
Peaks are samples at least 5 dB above that sweep's average, with hits closer
than 50 kHz dropped. Power is relative dBm, not a calibrated measurement.

```bash
./run_sweeper.sh --sweep --device 0 --start 118 --stop 137 --step 25
./run_sweeper.sh --sweep --device 1 --start 88 --stop 96 --step 200 --gain 30
./run_sweeper.sh --sweep --device 1 --gain auto
```

| Flag | Default | Meaning |
|---|---|---|
| `--device` | `0` | Dongle index. This host: `0` serial `00000978`, `1` serial `00001090`. |
| `--sweep` | off | One band sweep and a text report. Without it, opens a pyqtgraph live spectrum (needs `pyqtgraph` and `PyQt5`). |
| `--start` / `--stop` | `88` / `108` | Band edges in MHz. |
| `--step` | `500` | Step in kHz. |
| `--gain` | `40` | Tuner gain in dB, or `auto`. Clamped to the tuner's supported gains. |

## Visualize and zoom

```bash
./run_viewer.sh --device 1
```

Open http://127.0.0.1:8765. Plotly.js loads from a CDN, so the browser needs
network access the first time.

1. Set start, stop, and step (defaults 88 MHz, 108 MHz, 500 kHz) and click **Run sweep**.
2. The page shows power versus frequency, a heatmap of the last 40 sweeps, and a detected-signal list.
3. Click a detected signal. The same dongle retunes to that frequency and returns a narrowband spectrum, a short FFT waterfall, the peak frequency, and a −6 dB bandwidth.

```bash
./run_viewer.sh --device 0 --port 8080
./run_viewer.sh --device 1 --gain 35 --sample-rate 2400000
```

| Flag | Default | Meaning |
|---|---|---|
| `--device` | `0` | Dongle index. One viewer process holds one dongle. |
| `--port` | `8765` | HTTP port. Use a second port to run both dongles at once. |
| `--gain` | `40` | Tuner gain in dB, or `auto`. |
| `--sample-rate` | `2400000` | Sample rate in Hz. 2.4 MSPS is the stable default. |

## One-time setup

Python deps live in `.venv`. Install with uv, not system `pip`:

```bash
uv venv
uv pip install numpy pyrtlsdr pytest sounddevice websockets
uv pip install pyqtgraph pyqt5   # only for the live pyqtgraph window
```

Build newer `librtlsdr` into `~/.local` (see README.md). Confirm:

```bash
LD_LIBRARY_PATH="$HOME/.local/lib" .venv/bin/python -c "from rtlsdr import RtlSdr; print('lib OK')"
```

## Dongle busy

`LIBUSB_ERROR_BUSY` means another process has the device. Stop competing services before `scan` or `listen`. The radio CLI prints the error from `/api/health` and exits non-zero.

## Tests

```bash
.venv/bin/python -m pytest
```

The suite does not open a dongle.
