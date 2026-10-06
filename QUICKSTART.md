# Quickstart

Use the **radio backend** (`run_radio.sh`) for scan, listen, play, and monitor. Legacy text sweeps and the browser viewer are below. All wrapper scripts set `LD_LIBRARY_PATH=$HOME/.local/lib` so pyrtlsdr loads the newer `librtlsdr` in `~/.local` instead of the system library. Run commands from this directory.

## One-time setup

```bash
uv venv
uv pip install -r requirements.txt
uv pip install pyqtgraph pyqt5   # optional: live pyqtgraph window only (run_sweeper.sh without --sweep)
```

If import fails with a missing `rtlsdr_set_dithering` symbol, build newer librtlsdr into `~/.local` (see README.md), then:

```bash
LD_LIBRARY_PATH="$HOME/.local/lib" .venv/bin/python -c "from rtlsdr import RtlSdr; print('lib OK')"
```

`run_radio.sh`, `run_sweeper.sh`, and `run_viewer.sh` export `LD_LIBRARY_PATH` for you.

## First radio session

The CLI talks HTTP only; the dongle is opened by `radio_service.py` in the service process. Subcommands auto-start `./run_radio.sh` if nothing is listening on port 8766 (waits up to ~10 s for `/api/health`).

**Option A — start the service explicitly (one terminal):**

```bash
./run_radio.sh --device 0          # http://127.0.0.1:8766, dongle 0
# or
./run_radio.sh --device 1          # same port, dongle 1
```

**Option B — let `scan` / `listen` / `play` / `monitor` start it** (pass `--device` on the subcommand).

Typical first checks:

```bash
./run_radio.sh scan                         # default band: activity (118–174 MHz), device 0
./run_radio.sh scan --band fm --device 1    # FM broadcast 88–108 MHz on dongle 1
./run_radio.sh listen --freq 88.5 --device 0
./run_radio.sh play --band fm --device 1    # scan, then type a hit number at the prompt
./run_radio.sh monitor --device 0           # auto-dwell on nearby chatter
```

Global flags (also accepted on each subcommand): `--device` (default `0`), `--port` (default `8766`).

### What `scan` prints

Each line is one hit: index, MHz, relative power (dB), −6 dB bandwidth (kHz), band label, and reason in parentheses. Sorted by reason priority, then strongest power within each group. Empty band: `No hits.`

Example shape (values depend on what is on air):

```
  1    144.3900 MHz   -80.0 dB    15.0 kHz  nfm_voice  (hunt)
  2    100.0000 MHz   -40.0 dB   180.0 kHz  fm_broadcast  (active)
```

### What “interesting” means (`reason`)

Detection uses FFT bins per 2.4 MHz hop; a bin must sit at least 5 dB above the hop median. Hits closer than 50 kHz merge. Reasons:

| Reason | Meaning |
|--------|---------|
| `hunt` | Within 25 kHz of a watch frequency (121.5 MHz emergency, 144.39/145.8/146.52 MHz ham, marine weather 162.4–162.55 MHz, 433.92/446 MHz, 1090 MHz ADS-B, etc.). |
| `narrow_in_wide` | Label `fm_broadcast` but −6 dB bandwidth under 50 kHz (narrow carrier in the FM band). |
| `voice` | Label `airband_am` or `nfm_voice` (air, marine, ham). |
| `active` | Other detections that passed the threshold. |

Power is relative to each hop’s spectrum, not calibrated dBm in the field.

### `listen`

Required: `--freq` in MHz. Optional: `--mode` `wfm` | `nfm` | `am` (if omitted, mode follows the band label: WFM for FM broadcast, AM for airband, NFM otherwise).

Before audio, the CLI calls zoom and prints one line: `MHz` (four decimal places), −6 dB width in kHz, and `classify` label — e.g. after `--freq 88.5` you might see `fm_broadcast` with a width taken from `bandwidth_hz` in the zoom response. Palmetto frequencies exit before audio (SDRTrunk path, exit 2).

Audio is 48 kHz mono PCM over WebSocket (`/api/audio`); playback needs `sounddevice`. Stop with Ctrl+C (sends `POST /api/listen/stop`).

### `play`

Runs the same sweep as `scan`, prints the hit table, then prompts:

```
Hit number (q to quit): 2
```

Chooses demod mode from the hit’s label. Empty sweep: `No hits.` and exit 0.

### Monitor nearby chatter

Continuous Plan B loop: rotate communication-heavy bands (`CHATTER_BANDS` in `bands.py` — air, MURS, marine/rail, 2 m, ISM 433, 70 cm, GMRS, plus Palmetto for ID only), track occupancy across sweeps, auto-dwell on likely burst chatter, play brief audio, then resume.

```bash
./run_radio.sh monitor --device 0
```

On each candidate: prints `MHz  label  duty=…  (reason)`, `POST /api/listen`, plays WebSocket audio until quiet (~1.5 s zoom checks) or max dwell (~9 s), then `POST /api/listen/stop` and continues. Skips Palmetto 800 (prints SDRTrunk path if forced) and sticky persistent FM. Ctrl+C stops cleanly.

Needs `sounddevice` (same as `listen` / `play`).

### Named bands (`--band`)

MHz ranges from `bands.py`:

| Band | Range (MHz) |
|------|-------------|
| `fm` | 88 – 108 |
| `air` | 118 – 137 |
| `ham2m` | 144 – 148 |
| `ham70cm` | 420 – 450 |
| `activity` | 118 – 174 (default when `scan` / `play` omit `--band` and `--start`) |

Custom range: `--start` and `--stop` in MHz (both required if either is set). Applies to `scan` and `play`; the HTTP API uses the same MHz query params.

### Palmetto 800

Analog listen on Palmetto 800 downlinks (769–775 MHz and 851–861 MHz) is refused. The service returns HTTP 409 on `POST /api/listen`; the CLI prints the SDRTrunk path and exits 2:

```
/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk
```

### HTTP API (127.0.0.1:8766)

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | `device`, `open`, `error` (e.g. dongle busy message). |
| GET | `/api/sweep?start=&stop=` | `start` / `stop` in **MHz**; omit both for activity band. JSON `{ "hits": [...] }`. |
| GET | `/api/zoom?freq_hz=` | `freq_hz` in Hz; spectrum, waterfall, peak, bandwidth, label. |
| POST | `/api/listen` | Body `{"freq_hz": <Hz>, "mode": "wfm"|"nfm"|"am"}`. |
| POST | `/api/listen/stop` | Stops demod / releases tune. |
| GET | `/api/audio` | WebSocket upgrade; first text frame is JSON metadata, then s16le PCM. |

Examples:

```bash
curl -s http://127.0.0.1:8766/api/health
curl -s 'http://127.0.0.1:8766/api/sweep?start=88&stop=108'
curl -s 'http://127.0.0.1:8766/api/zoom?freq_hz=88500000'
```

## Legacy: text sweeper

```bash
./run_sweeper.sh --sweep --device 1
```

One-shot 88–108 MHz at 500 kHz steps (defaults). Peaks: ≥5 dB above sweep average, 50 kHz separation. `--start` / `--stop` / `--step` / `--gain` (dB or `auto`). Without `--sweep`, opens pyqtgraph live spectrum (needs optional PyQt packages).

## Legacy: browser viewer

```bash
./run_viewer.sh --device 1
```

Open http://127.0.0.1:8765 (different API than the radio backend: sweep uses `step`, zoom uses `frames`). Defaults: port 8765, gain 40, sample rate 2.4 MSPS.

## Dongle busy

`LIBUSB_ERROR_BUSY` in `/api/health` means another process holds the USB device. On this host, `dump1090-mutability` may use a dongle (`sudo systemctl stop dump1090-mutability`). The radio CLI prints the health `error` string and exits non-zero.

This host: device `0` serial `00000978`, device `1` serial `00001090`.

## Tests

```bash
.venv/bin/python -m pytest
```

No dongle required.
