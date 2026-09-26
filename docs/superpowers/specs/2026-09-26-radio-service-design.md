# Radio service design

Date: 2026-09-26

## Intent

Ship a backend you can use today, then a UI that calls the same API. The first build scans a band, lists the interesting hits, and plays analog audio on the speakers. The browser is the next build. It is not part of this one.

Success for the backend looks like this:

- `./run_radio.sh` starts the service on `127.0.0.1`.
- `./run_radio.sh scan --band fm` prints ranked hits: frequency, relative dBm, bandwidth, band label. A live dongle shows the known 88.50 MHz FM carrier.
- `./run_radio.sh listen --freq 88.5 --mode wfm` plays that station on the speakers until Ctrl+C.
- `./run_radio.sh play --band fm` scans, numbers the hits, and listens to the number you type.
- A Palmetto 800 frequency returns HTTP 409. The CLI prints that SDRTrunk is the player and does not demodulate.
- The source carries `TODO` comments for the follow-on work listed below. Those comments are the backlog. They are not implementations.
- pytest covers demodulation, band labels, the interesting-hit rule, and the 409 with synthetic IQ. Opening a real dongle stays a manual check.

## What you asked for

A) Get the backend working, including the HTTP API a later UI will call.

B) After that backend works, add a UI that consumes those routes. Do not start the UI in this build.

C) Leave `TODO` comments in the modules that would grow, for SDRTrunk, clear P25 and DMR demodulation, JMBE, and the other enhancements below.

Also: hear FM broadcast, narrow FM voice, and airband AM. Write docstrings and comments that say why. Update README.md and QUICKSTART.md in the same change that adds the command.

## Constraints

- Host is Ubuntu. Two RTL2838 dongles with R820T/2 tuners, index 0 serial `00000978` and index 1 serial `00001090`. The backend opens one of them.
- pyrtlsdr 0.5.0 imports `rtlsdr.RtlSdr`. The system `librtlsdr` lacks `rtlsdr_set_dithering`. `~/.local/lib` holds a build that has the symbol. `run_radio.sh` exports `LD_LIBRARY_PATH`.
- libusb calls stay on the thread that opened the device. The HTTP handler does not call `read_samples` itself. It posts work to that thread. Do not use `ThreadingHTTPServer`.
- Power figures are relative dBm, offset from `NOISE_FLOOR_DBM` (−95). They compare frequencies. They are not calibrated field strength.
- `RtlSDRSweeper` and `AdvancedSweeper` stay the capture and FFT primitives.

## Backend

One process owns the dongle and serves the API. The CLI is a client of that API, which is the same contract the UI will use.

```
dongle owner thread
        |
   radio_service  -- HTTP + PCM WebSocket on 127.0.0.1:8766
        |
   radio.py (scan / listen / play) -- sounddevice speakers
```

`run_radio.sh` sets `LD_LIBRARY_PATH`. With no subcommand it starts the service. `scan`, `listen`, and `play` start the service if it is not already up, call it, and leave it running.

| Command | What it does |
|---|---|
| `./run_radio.sh` | Serve the API. Default device 0, port 8766. |
| `scan --band fm` | One FFT sweep of 88–108 MHz. Print hits, strongest first. |
| `scan --start 118 --stop 137` | Same, edges in MHz. |
| `listen --freq 88.5 --mode wfm` | Tune, demodulate, play until Ctrl+C. |
| `play --band fm` | Scan, number the hits, listen to the index you type. `q` quits. |
| `--device 0\|1` | Which dongle. Default 0. |

Named bands: `fm` (88–108), `air` (118–137), `ham2m` (144–148), `ham70cm` (420–450). Default sample rate 2.4 MHz. Each hop is one sample-rate wide.

A hit is interesting when a bin is at least 5 dB above the median power of that hop and at least 50 kHz from the previous hit. Sort strongest first.

`listen` fills in `--mode` from the label when you omit it: `fm_broadcast` → `wfm`, `airband_am` → `am`, other analog labels → `nfm`. `play` does that. `palmetto800` does not get a mode.

Before audio starts, `listen` prints the tuned frequency, the −6 dB bandwidth, and the label.

## API

Bound to `127.0.0.1` only. This is the contract the UI build will call. Do not change the paths when the UI arrives. Add routes. Do not rename these.

- `GET /api/sweep?start=&stop=` — `start` and `stop` are MHz. Returns hits and one power sample per hop: frequency Hz, relative dBm, bandwidth Hz, band label, `is_new`. Floats are JSON numbers, not `np.float64`.
- `GET /api/zoom?freq_hz=` — retune, return spectrum dB relative to the peak, a short waterfall, peak Hz, bandwidth Hz, and the band label.
- `POST /api/listen` body `{freq_hz, mode}` with `mode` one of `wfm`, `nfm`, `am`. `palmetto800` returns 409 and the label. No audio starts.
- `POST /api/listen/stop` — stop demod and release the tune. The device stays open for the next sweep.
- `GET /api/audio` — WebSocket. Binary PCM s16le, 48 kHz, mono. The CLI plays this with `sounddevice`.
- `GET /api/health` — device index, whether the dongle opened, and the last error string.

Hop spacing is the sample rate. The `step` query from the old viewer is not part of this API.

## Modules

| Module | Responsibility |
|---|---|
| `demod.py` | FM discriminator, AM envelope, low-pass, resample to 48 kHz. No USB. |
| `bands.py` | Maps Hz to `fm_broadcast`, `airband_am`, `nfm_voice`, `palmetto800`, or `unknown`, and suggests a listen mode. |
| `scanner.py` | Hops an open sweeper. Returns hits. Called on the dongle thread. |
| `tuner.py` | Retunes, measures bandwidth, demodulates IQ to PCM. Called on the dongle thread. |
| `radio_service.py` | Dongle thread, HTTP, WebSocket, `TODO` markers for later work. |
| `radio.py` | CLI client. Speakers. No USB. |

`signal_viewer.py` stays as the existing viewer. This build does not change `viewer.html`.

## Listening

| Mode | Use |
|---|---|
| `wfm` | 88–108 MHz broadcast. Audio bandwidth about 15 kHz after demod. Stereo multiplex is ignored. |
| `nfm` | Ham, FRS, GMRS, marine. Deviation about 2.5–5 kHz. |
| `am` | 118–137 MHz airband. |

Speakers are the default `sounddevice` output, 48 kHz, mono, int16. Ctrl+C calls `/api/listen/stop` and closes the CLI. The service keeps running.

## TODO comments to leave in the code

Put these in `radio_service.py` next to the function they would extend. Each one is a comment, not a stub that pretends to work. Wording can be shorter than this list. The point of each item stays.

1. **SDRTrunk incorporation.** Launcher is `/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk`. User data is `/home/jb/SDRTrunk/` (`configuration/tuner_configuration.json`, `playlist/default.xml`, `recordings/`). SDRTrunk opens the dongle with usb4java and accepts no tune command. Later work can detect that it is running, avoid the USB index it holds, and list files it already wrote under `recordings/`. It does not remote-control SDRTrunk.
2. **P25 and DMR demodulation.** Clear P25 (Palmetto 800 is P25 CAI, 769–775 MHz and 851–861 MHz downlink) and clear DMR need their own demodulators. Analog NFM on those carriers is not speech. Do not implement them in this build. Do not read, guess, or store encryption keys. Encrypted talkgroups stay inside SDRTrunk, where a key the operator already configured can play them.
3. **JMBE.** `/home/jb/SDRTrunk/jmbe` turns clear P25 IMBE frames into PCM. A later demodulator calls that vocoder. It is not an encryption bypass.
4. **Second dongle.** Keep an FFT sweep running on device 0 while device 1 plays audio.
5. **Peak log and alert.** Append timestamp, Hz, relative dBm, and label to a file. Notify when `is_new` flips on.
6. **Max-hold.** A transmission shorter than the hop time disappears. Keep the max per bin for a few passes.
7. **Stereo WFM.** Decode the 19 kHz pilot and the L−R baseband. The first build is mono.
8. **Finer names.** Map an FM hit to a channel number, and a ham hit to a calling frequency when it lands on one. The first build only has the five labels above.

## UI, after the backend works

A later change adds a page that calls the routes above. It shows the sweep, the hit list, a zoom from `/api/zoom`, and audio from `/api/audio`. It does not open a dongle and it does not add a second backend. That work starts only after `scan` and `listen` work against a real dongle.

## Palmetto 800 and SDRTrunk

Palmetto 800 is South Carolina's statewide Motorola P25 system (RadioReference system 5042, P25 CAI). Site control channels differ. NFM on those carriers does not produce speech.

The saved tuner file has three R820T configs, centers 859.2375 MHz, 852.8625 MHz, and 101.1 MHz. The first two sit in the 800 MHz public-safety downlink. While SDRTrunk is running it holds those USB devices.

This build:

- Labels 769–775 MHz and 851–861 MHz as `palmetto800`.
- Returns 409 from `POST /api/listen` and prints the SDRTrunk hint.
- On `LIBUSB_ERROR_BUSY`, names the device index and exits the command non-zero. It does not kill SDRTrunk.

## Error handling

- Missing or old `librtlsdr`: `/api/health` reports the error. `scan` and `listen` print it and exit non-zero.
- `LIBUSB_ERROR_BUSY`: name the index. Do not retry in a loop.
- A second listen or zoom cancels the current demod, then retunes. One owner thread.
- Empty IQ: demod returns silence, not NaN.
- Last WebSocket client gone for 2 seconds: stop demod so a dead CLI does not hold the tune.
- Ctrl+C in the CLI: `POST /api/listen/stop`, then exit. The service process stays up.

## Testing

pytest, no hardware, fake `RtlSdr` where a device object is required.

- `demod.py`: a synthesized NFM tone comes back near the modulating frequency. An AM tone comes back. Silence in, silence out. PCM length matches 48 kHz.
- `bands.py`: 88.5 MHz is `fm_broadcast` / `wfm`. 121.5 MHz is `airband_am` / `am`. 146.52 MHz is `nfm_voice` / `nfm`. 852.35 MHz and 773.66 MHz are `palmetto800` with no mode. 433.92 MHz is `unknown` / `nfm`.
- Scanner: a bin 5 dB above the hop median is a hit. A quieter bin is not. Two hits 20 kHz apart collapse to one.
- API: `POST /api/listen` at 852.35 MHz returns 409. `GET /api/sweep` against a fake dongle returns JSON-safe floats.

`.venv/bin/python -m pytest` stays the command. The manual check in QUICKSTART is `./run_radio.sh play --band fm --device 1` and hearing 88.50 MHz.

## Documentation

Every new module gets a module docstring that says what it owns and which thread may touch the dongle. Comments explain why a hop is one sample-rate wide, why Palmetto hits are not demodulated here, and why each `TODO` is waiting.

The change that adds `run_radio.sh` updates README.md and QUICKSTART.md with the commands, the band names, the API base URL, and the busy-dongle case.

## Out of scope for this build

- Changes to `viewer.html` or a new browser page.
- Implementing the `TODO` items above.
- Driving SDRTrunk, or parsing its playlist.
- Reading, storing, or deriving encryption keys.
- Calibrated dBm.
- Binding the HTTP port to anything but localhost.

## Implementation gate

This file is the spec. Implementation waits until this revision is approved and an implementation plan exists.
