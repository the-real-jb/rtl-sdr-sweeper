# Radio service design

Date: 2026-09-26

## Intent

Find interesting signals and listen to them from the command line. The first slice is a backend you can play with: scan a band, print the hits, pick one, and hear it on this machine's speakers. A browser viewer is a later client of the same objects. It is not part of the first slice.

Success for the first slice looks like this:

- `./run_radio.sh scan --band fm` prints ranked hits with frequency, relative dBm, bandwidth, and a band label. The known 88.50 MHz FM carrier shows up on a live dongle.
- `./run_radio.sh listen --freq 88.5 --mode wfm` plays that station on the speakers until Ctrl+C.
- `./run_radio.sh play --band fm` scans, prints a numbered list, and listens to the number you type.
- A Palmetto 800 hit is labeled and refused for analog listen. The command tells you to use SDRTrunk.
- pytest covers demodulation, band labels, and the interesting-hit rule with synthetic IQ. Opening a real dongle stays a manual check.

The longer-term service (a scanner thread that keeps running while a second dongle listens, then an HTTP API and a browser) stays the target architecture. Do not build it in the first slice.

## What you asked for

Find signals, investigate them, and listen. Hear FM broadcast, narrow FM voice, and airband AM. Identify unknown signals. Treat Palmetto 800 audio as something SDRTrunk already does. Write comments and docstrings, and keep README.md and QUICKSTART.md current as the code lands. Prefer a working backend over a UI.

## Constraints

- Host is Ubuntu. Two RTL2838 dongles with R820T/2 tuners, index 0 serial `00000978` and index 1 serial `00001090`.
- pyrtlsdr 0.5.0 imports `rtlsdr.RtlSdr`. The system `librtlsdr` lacks `rtlsdr_set_dithering`. `~/.local/lib` holds a build that has the symbol. Launch scripts export `LD_LIBRARY_PATH`.
- libusb calls for a device stay on the thread that opened it. In the first slice that thread is the CLI main thread, because only one dongle is open at a time.
- Power figures are relative dBm, offset from `NOISE_FLOOR_DBM` (−95). They compare frequencies. They are not calibrated field strength.
- `RtlSDRSweeper` and `AdvancedSweeper` stay the capture and FFT primitives. The new modules call them.

## First slice

One dongle. One process. No HTTP server.

```
scan_band() -> list of hits
listen(freq, mode) -> PCM blocks written to the speakers
```

`run_radio.sh` sets `LD_LIBRARY_PATH` and runs `radio.py`.

| Command | What it does |
|---|---|
| `scan --band fm` | One FFT sweep of 88–108 MHz. Prints hits, strongest first. |
| `scan --start 118 --stop 137` | Same, with explicit edges in MHz. |
| `listen --freq 88.5 --mode wfm` | Tune, demodulate, play until Ctrl+C. |
| `play --band fm` | Scan, number the hits, listen to the index you type. `q` quits. |
| `--device 0\|1` | Which dongle. Default 0. |

Named bands: `fm` (88–108), `air` (118–137), `ham2m` (144–148), `ham70cm` (420–450). Default sample rate 2.4 MHz. Each hop is one sample-rate wide, so FM is about ten tunes, not a 100 kHz stepped power sweep.

A hit is interesting when a bin is at least 5 dB above the median power of that hop and at least 50 kHz from the previous hit. The command prints the band label next to it. Sorting is by power, descending.

`listen` picks the mode from the label when you omit `--mode`: `fm` → `wfm`, `air` → `am`, anything else analog → `nfm`. `play` does that automatically. `palmetto800` prints the SDRTrunk hint and does not open the demodulator.

Investigate, without a plot: `listen` prints the tuned frequency, the −6 dB bandwidth, and the label before audio starts. That is the check that a wide carrier is broadcast FM and a narrow one is voice.

## Components (first slice)

| Module | Responsibility | Depends on |
|---|---|---|
| `demod.py` | FM discriminator, AM envelope, low-pass, resample to 48 kHz PCM. No USB. | numpy, scipy |
| `bands.py` | Maps Hz to `fm_broadcast`, `airband_am`, `nfm_voice`, `palmetto800`, or `unknown`. Suggests a listen mode. | nothing |
| `scanner.py` | Hops one open sweeper across a band. Returns hits. Not a background thread in this slice. | `AdvancedSweeper.analyze_with_fft`, `bands.py` |
| `tuner.py` | Retunes, measures bandwidth, demodulates IQ into PCM blocks. | `AdvancedSweeper`, `demod.py`, `bands.py` |
| `radio.py` | CLI for `scan`, `listen`, and `play`. Opens one dongle, plays PCM with `sounddevice`, closes the dongle in `finally`. | scanner, tuner |

## Listening

| Mode | Use |
|---|---|
| `wfm` | 88–108 MHz broadcast. Audio bandwidth about 15 kHz after demod. Stereo multiplex is ignored. |
| `nfm` | Ham, FRS, GMRS, marine. Deviation about 2.5–5 kHz. |
| `am` | 118–137 MHz airband. |

Speakers come from `sounddevice`, default output device, 48 kHz, mono, int16. Ctrl+C stops playback and closes the dongle.

## Later, not in this slice

Keep these names stable so a UI can call the same functions, but do not implement them now:

- A scanner thread on dongle 0 that keeps sweeping while dongle 1 listens.
- HTTP on `127.0.0.1`: `/api/sweep`, `/api/zoom`, `/api/listen`, `/api/log`, and a PCM WebSocket.
- Browser heatmap, click-to-zoom, and in-browser audio.
- A second speaker client over that socket.
- Tailing `/home/jb/SDRTrunk/recordings/`.

`signal_viewer.py` stays as it is. The first slice does not replace it.

## Palmetto 800 and SDRTrunk

Palmetto 800 is South Carolina's statewide Motorola P25 system (RadioReference system 5042, P25 CAI). Site channels sit in the 700 MHz downlink, about 769–775 MHz, and the 800 MHz downlink, about 851–861 MHz. Control channels are per site. NFM demodulation of those carriers does not produce speech.

Installed pieces on this host:

- Launcher: `/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk` (v0.6.1).
- User data: `/home/jb/SDRTrunk/`, including `configuration/tuner_configuration.json`, `playlist/default.xml`, and `recordings/`.
- SDRTrunk opens the dongle with usb4java. It has no rtl_tcp client and no HTTP API that accepts a tune command. `/home/jb/SDRTrunk/REMOTE-SDR.md` records that fact.
- The saved tuner file lists three R820T configs, centers 859.2375 MHz, 852.8625 MHz, and 101.1 MHz. The first two are inside the 800 MHz public-safety downlink. While SDRTrunk is running it holds those USB devices.

First-slice behavior:

- `bands.py` labels 769–775 MHz and 851–861 MHz as `palmetto800`.
- `listen` and `play` refuse that label and print that SDRTrunk is the player. They do not demodulate.
- A busy dongle prints which index failed and why. This project does not kill SDRTrunk.

JMBE, under `/home/jb/SDRTrunk/jmbe`, turns clear P25 IMBE frames into PCM. That is vocoder decode. Some Palmetto talkgroups are encrypted. Encrypted audio plays only if a key is already configured inside SDRTrunk. This project does not read those keys, guess keys, or implement a decryptor.

## Error handling

- Missing or old `librtlsdr`: the CLI prints the same clear message the sweeper uses today and exits non-zero. It does not start a server.
- `LIBUSB_ERROR_BUSY`: name the device index and exit non-zero. Do not retry in a loop.
- Empty or clipped IQ: demod returns silence, not a NaN buffer.
- Ctrl+C during `listen` or `play`: close the dongle before the process exits.

## Testing

pytest, no hardware, fake `RtlSdr` where a device object is required.

- `demod.py`: a synthesized NFM tone comes back near the modulating frequency. An AM tone comes back. Silence in, silence out. PCM length matches 48 kHz.
- `bands.py`: 88.5 MHz is `fm_broadcast` with mode `wfm`. 121.5 MHz is `airband_am` with mode `am`. 146.52 MHz is `nfm_voice`. 852.35 MHz and 773.66 MHz are `palmetto800` with no listen mode. 433.92 MHz is `unknown` with mode `nfm`.
- Scanner: a bin 5 dB above the hop median is a hit. A quieter bin is not. Two hits 20 kHz apart collapse to one.
- `radio.py`: choosing a `palmetto800` hit does not call the demodulator. `scan` output is sorted strongest first.

`.venv/bin/python -m pytest` stays the command. The manual check in QUICKSTART is `./run_radio.sh play --band fm --device 1` and confirming you can hear 88.50 MHz.

## Documentation

Every new module gets a module docstring stating what it owns. Functions that hide a unit or a USB rule get a docstring. Comments explain why a hop is one sample-rate wide and why a Palmetto hit is not demodulated here.

The same change that adds `run_radio.sh` updates README.md and QUICKSTART.md with `scan`, `listen`, `play`, the band names, and the busy-dongle case.

## Out of scope for the first slice

- HTTP, WebSocket, and any change to `viewer.html`.
- Driving SDRTrunk or sending it a frequency.
- P25, DMR, or ADS-B demodulation inside this process.
- Reading, storing, or deriving encryption keys.
- Stereo multiplex decode for WFM.
- Calibrated dBm.
- Scanning on one dongle while listening on the other.

## Implementation gate

This file is the spec. Implementation waits until this revision is approved and an implementation plan exists.
