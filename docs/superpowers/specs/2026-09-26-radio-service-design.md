# Radio service design

Date: 2026-09-26

## Intent

Run a background service that keeps scanning while you watch, click a signal, and listen. The same process logs peaks when nobody is at the desk. The browser is the live console. A local player can send the same audio to this machine's speakers.

Success looks like this:

- One dongle keeps an FFT sweep running on the selected band.
- A second dongle retunes to a clicked frequency, shows a narrowband spectrum and waterfall, and plays WFM, NFM, or AM audio in the browser. A local player can play that same stream.
- A hit that sits at least 5 dB over the rolling per-bin baseline, and was absent on the previous pass, is stored and marked new.
- A hit in the Palmetto 800 downlink is labeled P25 and left for SDRTrunk. This service does not try to speak analog audio from it.
- Tests cover demodulation, bandwidth, and the new-peak rule with synthetic IQ. Opening a real dongle stays a manual check.

## What you asked for

Find signals, investigate them, and listen. Use both dongles. Hear FM broadcast, narrow FM voice, and airband AM. Identify unknown signals. Treat digital trunked audio as something SDRTrunk already does. Write comments and docstrings, and keep README.md and QUICKSTART.md current as the code lands.

## Constraints

- Host is Ubuntu. Two RTL2838 dongles with R820T/2 tuners, index 0 serial `00000978` and index 1 serial `00001090`.
- pyrtlsdr 0.5.0 imports `rtlsdr.RtlSdr`. The system `librtlsdr` lacks `rtlsdr_set_dithering`. `~/.local/lib` holds a build that has the symbol. `run_sweeper.sh` and `run_viewer.sh` export `LD_LIBRARY_PATH`.
- libusb calls for a device stay on the thread that opened it.
- Power figures are relative dBm, offset from `NOISE_FLOOR_DBM` (−95). They compare frequencies. They are not calibrated field strength.
- `RtlSDRSweeper` and `AdvancedSweeper` stay the capture and FFT primitives. The service calls them. Clients do not open a dongle.

## Architecture

One radio service process owns every dongle this project opens. The browser and the local player are clients of that process.

```
Dongle 0 scanner          Dongle 1 tuner
        \                      /
         radio service
         (one owner thread per dongle)
              |        \
         HTTP + WS     peak log
              |
     browser viewer, optional local player
```

The scanner never stops because someone clicked. The tuner does zoom and demodulation. If only one dongle is free, listening pauses the sweep and the sweep resumes when listening stops.

SDRTrunk is a separate program. It opens RTL dongles itself through usb4java. This service does not start it, stop it, or retune it.

## Components

| Module | Responsibility | Depends on |
|---|---|---|
| `scanner.py` | One thread. Hops the scanner dongle by about one sample-rate width (2.4 MHz at the default rate). FFTs each hop into bins near 10 kHz. Keeps a per-bin baseline. Emits peaks. | `AdvancedSweeper.analyze_with_fft` |
| `tuner.py` | One thread. Retunes, returns spectrum, waterfall, peak Hz, and −6 dB bandwidth. Then demodulates WFM, NFM, or AM into 48 kHz PCM. | `AdvancedSweeper`, `demod.py` |
| `demod.py` | FM discriminator, AM envelope, low-pass, resample. No USB. | numpy, scipy |
| `bands.py` | Maps a frequency to a label: FM broadcast, airband AM, narrow voice, Palmetto 800 / P25, unknown. | nothing |
| `radio_service.py` | Owns the threads, HTTP and WebSocket API, peak log, new-peak flag. | scanner, tuner, bands |
| `viewer.html` (updated) | Sweep heatmap, peak list, click to zoom, mode select, audio play. | the API |
| `play_local.py` | Optional client. Reads the audio WebSocket and writes the host speakers. | the API |

`signal_viewer.py` becomes a thin launcher for `radio_service.py`, or is replaced by it. `freq_sweeper_full.py` and `freq_sweeper_advanced.py` keep their CLI sweeps.

## API

Bound to `127.0.0.1` only.

- `GET /api/sweep` returns the latest FFT grid, the baseline, and peaks. Query params `start`, `stop` (MHz) and `step` are ignored for hop spacing. Hop spacing is the sample rate. The client still sends the band edges.
- `GET /api/zoom?freq_hz=` retunes the tuner and returns the narrowband spectrum in dB relative to the peak, a short waterfall, peak Hz, bandwidth Hz, and the band label.
- `POST /api/listen` body `{freq_hz, mode}` with `mode` one of `wfm`, `nfm`, `am`. Starts demod on the tuner. Rejects the call with 409 and the band label when the frequency is Palmetto 800.
- `POST /api/listen/stop` releases the tuner and, in single-dongle mode, resumes the sweep.
- `GET /api/audio` WebSocket. Binary PCM s16le, 48 kHz, mono.
- `GET /api/log` returns stored peaks: timestamp, Hz, relative dBm, bandwidth Hz, band label, and `is_new`.

A peak is new when its bin is at least 5 dB above the rolling baseline and the previous sweep pass did not already mark that bin. The default separation between reported peaks stays 50 kHz, matching `detect_peaks`.

## Listening

| Mode | Use |
|---|---|
| `wfm` | 88–108 MHz broadcast. Audio bandwidth about 15 kHz after demod, stereo pilot ignored in v1. |
| `nfm` | Ham, FRS, GMRS, marine. Deviation about 2.5–5 kHz. |
| `am` | 118–137 MHz airband. |

The browser plays the WebSocket with the Web Audio API. `play_local.py` uses `sounddevice` and the same socket. One demodulator, two consumers.

## Palmetto 800 and SDRTrunk

Palmetto 800 is South Carolina's statewide Motorola P25 system (RadioReference system 5042, P25 CAI). Site channels sit in the 700 MHz downlink, about 769–775 MHz, and the 800 MHz downlink, about 851–861 MHz. Control channels are per site, not one statewide pair. NFM demodulation of those carriers does not produce speech.

Installed pieces on this host:

- Launcher: `/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk` (v0.6.1).
- User data: `/home/jb/SDRTrunk/`, including `configuration/tuner_configuration.json`, `playlist/default.xml`, and `recordings/`.
- SDRTrunk opens the dongle with usb4java. It has no rtl_tcp client and no HTTP API that accepts a tune command. A note in `/home/jb/SDRTrunk/REMOTE-SDR.md` records that fact.
- The saved tuner file lists three R820T configs, centers 859.2375 MHz, 852.8625 MHz, and 101.1 MHz. The first two are inside the 800 MHz public-safety downlink. While SDRTrunk is running it holds those USB devices.

v1 behavior:

- `bands.py` labels 769–775 MHz and 851–861 MHz as `palmetto800`.
- The viewer shows that label and does not offer analog listen.
- If `connect()` fails with a busy device, the service says so and names USB-busy as the cause. It does not kill SDRTrunk.

JMBE, under `/home/jb/SDRTrunk/jmbe`, turns clear P25 IMBE frames into PCM. That is vocoder decode. Some Palmetto talkgroups are encrypted. Encrypted audio plays only if a key is already configured inside SDRTrunk. This project does not read those keys, guess keys, or implement a decryptor.

A later hook may list files already written under `/home/jb/SDRTrunk/recordings/`. v1 does not tail that directory and does not parse the playlist.

## Error handling

- Missing or old `librtlsdr`: same message the sweeper prints today. `connect()` returns false. The HTTP process still starts and `/api/sweep` reports the device error.
- `LIBUSB_ERROR_BUSY`: report which index failed. Keep scanning on the index that opened.
- Tuner in use for listen, and a second zoom or listen arrives: cancel the current demod, then retune. Do not open the device from a second thread.
- Empty or clipped IQ: demod returns silence, not a NaN buffer. Peak math already guards `log10` of zero in `get_signal_power`.
- WebSocket client drops: drop that consumer. If no consumer remains for 2 seconds, and `POST /api/listen/stop` has not already run, demod stops and the tuner is released. A closed browser tab does not hold the dongle.

## Testing

pytest, no hardware, fake `RtlSdr` where a device object is required.

- `demod.py`: a synthesized NFM tone comes back near the modulating frequency. An AM tone comes back. Silence in, silence out. Length of the PCM block matches 48 kHz.
- `bands.py`: 88.5 MHz is FM broadcast, 121.5 MHz is airband AM, 146.52 MHz is narrow voice, 852.35 MHz and 773.66 MHz are `palmetto800`, 433.92 MHz is unknown.
- Scanner baseline: a bin that jumps 5 dB and was quiet last pass is new. The same bin on the next pass is not new. A bin under the threshold is dropped.
- Service: `POST /api/listen` on 852.35 MHz returns 409. A fake dongle sweep returns JSON-safe floats.

` .venv/bin/python -m pytest` stays the command. A live check, written in QUICKSTART, is `./run_viewer.sh` then a click on the known 88.50 MHz FM carrier.

## Documentation

Every new module gets a module docstring stating what it owns and which thread may touch the dongle. Functions that hide a unit or a USB rule get a docstring. Comments explain why a hop is one sample-rate wide and why Palmetto hits are not demodulated here.

When the service replaces the viewer entry point, update README.md and QUICKSTART.md in the same change: launch command, listen modes, the single-dongle fallback, and the SDRTrunk busy case. Do not leave the docs pointing at an API that no longer exists.

## Out of scope

- Driving SDRTrunk or sending it a frequency.
- P25, DMR, or ADS-B demodulation inside this process.
- Reading, storing, or deriving encryption keys.
- Stereo multiplex decode for WFM.
- Calibrated dBm.
- Opening the HTTP port on anything but localhost.

## Implementation gate

This file is the spec. Implementation waits until this file is approved and an implementation plan exists.
