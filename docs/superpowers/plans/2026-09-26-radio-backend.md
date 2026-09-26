# Radio backend implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a localhost API that sweeps for interesting hits and plays analog audio, with a CLI client and no browser changes.

**Architecture:** Pure functions in `bands.py`, `hits.py`, and `demod.py` decide what is interesting and how to demodulate. `scanner.py` and `tuner.py` call `AdvancedSweeper` on one dongle thread inside `radio_service.py`. `radio.py` is an HTTP client that plays PCM with `sounddevice`.

**Tech Stack:** Python 3.11, numpy, scipy, pyrtlsdr (already in `.venv`), stdlib `http.server` plus a tiny WebSocket implementation or the `websockets` package only if already present. Prefer the stdlib for HTTP. For the audio WebSocket, implement a minimal server in `radio_service.py` that speaks the `GET /api/audio` upgrade, or stream PCM as chunked `GET /api/audio` if the WebSocket handshake is the thing blocking tests. Spec requires a WebSocket. Use the `websockets` library via `uv pip install websockets` if import fails. Tests must not open a dongle.

**Spec:** `docs/superpowers/specs/2026-09-26-radio-service-design.md`

## Global Constraints

- Bind HTTP to `127.0.0.1` only. Default port `8766`.
- One dongle owner thread. Do not use `ThreadingHTTPServer`. Do not call `read_samples` from the HTTP thread.
- Import RTL as `from rtlsdr import RtlSdr`. Launch scripts set `LD_LIBRARY_PATH=$HOME/.local/lib`.
- Relative dBm uses `NOISE_FLOOR_DBM` from `freq_sweeper_full` (−95).
- `palmetto800` listen returns HTTP 409 and does not demodulate.
- `is_new` is always false in this build.
- Default scan with no band and no start/stop is `activity`, 118–174 MHz.
- Do not edit `viewer.html`.
- Leave the eight TODO comments from the spec in `radio_service.py`. Do not implement them.
- Docstrings on every new module. Comments where a USB rule or the DC-spur rule would otherwise look arbitrary.
- Update README.md and QUICKSTART.md in the task that adds `run_radio.sh`.

## Review Focus

- A sweep that passes `start` without `stop` (or the reverse) must return HTTP 400, not scan a partial band. Test in Task 5.
- `listen` of a Palmetto frequency must not call the demodulator even if the client also sends `mode=nfm`. Test in Task 5.
- Ctrl+C is not unit-tested. The CLI `finally` must still POST `/api/listen/stop`. Test the stop helper directly in Task 6.
- WebSocket binary frames are s16le mono 48 kHz. A test in Task 5 reads one frame from a fake tone and checks dtype and rate metadata in the hello JSON that precedes binary frames.
- DC spur at the hop center, 30 dB above the floor, is not a hit. Test in Task 2.

---

### Task 1: Band labels and hunt list

**Files:**
- Create: `bands.py`
- Test: `test_bands.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `HUNT_HZ: tuple[float, ...]`
  - `BANDS: dict[str, tuple[float, float]]` keys `fm`, `air`, `ham2m`, `ham70cm`, `activity`. Values are Hz edges.
  - `classify(freq_hz: float) -> str` returns `fm_broadcast`, `airband_am`, `nfm_voice`, `palmetto800`, or `unknown`.
  - `listen_mode(label: str) -> str | None` returns `wfm`, `am`, `nfm`, or `None` for `palmetto800`.
  - `hunt_match(freq_hz: float, tolerance_hz: float = 25_000.0) -> bool`

- [ ] **Step 1: Write the failing test**

```python
from bands import BANDS, classify, hunt_match, listen_mode

def test_classify_known_ranges():
    assert classify(88.5e6) == "fm_broadcast"
    assert classify(121.5e6) == "airband_am"
    assert classify(146.52e6) == "nfm_voice"
    assert classify(852.35e6) == "palmetto800"
    assert classify(773.66e6) == "palmetto800"
    assert classify(433.92e6) == "unknown"

def test_listen_mode():
    assert listen_mode("fm_broadcast") == "wfm"
    assert listen_mode("airband_am") == "am"
    assert listen_mode("nfm_voice") == "nfm"
    assert listen_mode("unknown") == "nfm"
    assert listen_mode("palmetto800") is None

def test_hunt_and_default_band():
    assert hunt_match(144.390e6)
    assert hunt_match(144.390e6 + 20_000)
    assert not hunt_match(144.390e6 + 40_000)
    assert BANDS["activity"] == (118e6, 174e6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_bands.py -v`
Expected: FAIL, `bands` not found

- [ ] **Step 3: Write minimal implementation**

`bands.py` holds the ranges from the spec, `HUNT_HZ` as the watch frequencies in the spec table, `classify` checking palmetto (769–775 and 851–861 MHz) before FM, air, marine 156–162 MHz as `nfm_voice`, ham 144–148 and 420–450. `listen_mode` as the test states. `hunt_match` uses absolute difference.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_bands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bands.py test_bands.py
git commit -m "Label frequencies and suggest a listen mode."
```

---

### Task 2: Interesting-hit picker

**Files:**
- Create: `hits.py`
- Test: `test_hits.py`

**Interfaces:**
- Consumes: `bands.classify`, `bands.hunt_match`
- Produces:
  - `Hit` dataclass: `freq_hz: float`, `power_db: float`, `bandwidth_hz: float`, `label: str`, `reason: str`, `is_new: bool`
  - `hits_from_spectrum(freq_hz: np.ndarray, power_db: np.ndarray, hop_center_hz: float, threshold_db: float = 5.0) -> list[Hit]`
  - `sort_hits(hits: list[Hit]) -> list[Hit]`

Power is already in dB. Width is the contiguous run of bins within 6 dB of the peak bin of that run. Two candidates merge when centers are closer than 50 kHz.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from hits import hits_from_spectrum, sort_hits

def _flat(n, center, fs=2_400_000):
    freqs = center + np.linspace(-fs / 2, fs / 2, n, endpoint=False)
    power = np.full(n, -100.0)
    return freqs, power

def test_floor_and_single_bin_spur():
    freqs, power = _flat(128, 100e6)
    power[40] = -90.0  # 10 dB up, one bin
    assert hits_from_spectrum(freqs, power, hop_center_hz=100e6) == []

def test_dc_spur_dropped():
    freqs, power = _flat(128, 100e6)
    mid = int(np.argmin(np.abs(freqs - 100e6)))
    power[mid - 1:mid + 2] = -70.0
    assert hits_from_spectrum(freqs, power, hop_center_hz=100e6) == []

def test_narrow_in_fm_band_and_hunt_sort():
    freqs, power = _flat(256, 100e6)
    # Narrow cluster away from DC, ~20 kHz class.
    power[20:23] = -80.0
    hits = hits_from_spectrum(freqs, power, hop_center_hz=100e6)
    assert len(hits) == 1
    assert hits[0].reason == "narrow_in_wide"
    assert hits[0].is_new is False

def test_hunt_beats_active_and_merge():
    freqs, power = _flat(512, 145e6, fs=2_400_000)
    # Two close bumps around APRS.
    i = int(np.argmin(np.abs(freqs - 144.390e6)))
    power[i:i + 3] = -70.0
    power[i + 4:i + 7] = -75.0
    hits = sort_hits(hits_from_spectrum(freqs, power, hop_center_hz=145e6))
    assert hits[0].reason == "hunt"
    assert len(hits) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_hits.py -v`
Expected: FAIL, `hits` not found

- [ ] **Step 3: Write minimal implementation**

Implement the spec rules. `power_db` compared to `np.median(power_db) + threshold_db`. Contiguous candidates above threshold form a run. Drop runs whose peak is within 25 kHz of `hop_center_hz` unless `hunt_match`. Drop runs of width 1 bin unless `hunt_match`. Bandwidth is the span of bins within 6 dB of the run peak. Reason order: hunt, narrow_in_wide (label `fm_broadcast` and bandwidth < 50_000), voice (label in `airband_am` or `nfm_voice`), else `active`. `sort_hits` uses the spec order then `-power_db`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_hits.py test_bands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hits.py test_hits.py
git commit -m "Pick interesting hits from a hop spectrum."
```

---

### Task 3: Analog demodulator

**Files:**
- Create: `demod.py`
- Test: `test_demod.py`

**Interfaces:**
- Consumes: nothing
- Produces: `demodulate(iq: np.ndarray, sample_rate: float, mode: str) -> np.ndarray` int16 mono at 48000 Hz. `mode` is `wfm`, `nfm`, or `am`. Empty input returns an empty int16 array.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from demod import demodulate

def test_silence_and_empty():
    assert demodulate(np.zeros(0, dtype=np.complex64), 48_000, "nfm").size == 0
    pcm = demodulate(np.zeros(4800, dtype=np.complex64), 48_000, "am")
    assert pcm.dtype == np.int16
    assert np.max(np.abs(pcm)) == 0

def test_am_tone_near_1khz():
    sr = 48_000
    t = np.arange(sr) / sr
    carrier = np.exp(1j * 2 * np.pi * 0 * t)  # baseband
    iq = (1.0 + 0.6 * np.sin(2 * np.pi * 1000 * t)) * carrier
    pcm = demodulate(iq.astype(np.complex64), sr, "am")
    spec = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    peak = np.argmax(spec[1:]) + 1
    hz = peak * (48_000 / len(pcm))
    assert 800 < hz < 1200

def test_nfm_tone_near_1khz():
    sr = 48_000
    t = np.arange(sr) / sr
    mod = np.sin(2 * np.pi * 1000 * t)
    phase = np.cumsum(2 * np.pi * 2500 * mod / sr)
    iq = np.exp(1j * phase).astype(np.complex64)
    pcm = demodulate(iq, sr, "nfm")
    spec = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    peak = np.argmax(spec[1:]) + 1
    hz = peak * (48_000 / len(pcm))
    assert 800 < hz < 1200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_demod.py -v`
Expected: FAIL, `demod` not found

- [ ] **Step 3: Write minimal implementation**

AM: `np.abs`, subtract mean, low-pass with `scipy.signal.butter` + `filtfilt` at 5 kHz (AM) or 4 kHz (NFM) or 15 kHz (WFM), then `resample_poly` to 48 kHz. NFM and WFM: `np.angle(iq[1:] * np.conj(iq[:-1]))` then the same filter. Scale peak to 0.8 of int16 range. Empty and all-zero input return zeros of the resampled length, or empty if input length is 0. Guard NaNs by `np.nan_to_num`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_demod.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add demod.py test_demod.py
git commit -m "Demodulate AM and FM to 48 kHz PCM."
```

Install scipy first if missing: `uv pip install scipy`.

---

### Task 4: Scanner hop list

**Files:**
- Create: `scanner.py`
- Test: `test_scanner.py`

**Interfaces:**
- Consumes: `hits.hits_from_spectrum`, `hits.sort_hits`, `AdvancedSweeper.analyze_with_fft` only when a sweeper object is passed. The pure function does not open USB.
- Produces:
  - `hop_centers(start_hz: float, stop_hz: float, sample_rate: float) -> list[float]`
  - `scan_spectrum(sweep_power: list[tuple[np.ndarray, np.ndarray, float]]) -> list[Hit]` where each tuple is `(freq_hz, power_db, hop_center_hz)`.

`hop_centers` steps by `sample_rate * 0.8` so hops overlap and edge roll-off is not the only look at a carrier. First center is `start_hz + sample_rate / 2`. Last center is the greatest value whose window still covers `stop_hz`.

- [ ] **Step 1: Write the failing test**

```python
from scanner import hop_centers, scan_spectrum
import numpy as np

def test_hops_cover_activity_band():
    centers = hop_centers(118e6, 174e6, 2_400_000)
    assert centers[0] == 118e6 + 1_200_000
    assert centers[-1] + 1_200_000 >= 174e6

def test_scan_spectrum_sorts_hunt_first():
    freqs = np.linspace(144e6, 146e6, 64, endpoint=False)
    power = np.full(64, -110.0)
    i = int(np.argmin(np.abs(freqs - 144.390e6)))
    power[i:i + 3] = -80.0
    hits = scan_spectrum([(freqs, power, 145e6)])
    assert hits[0].reason == "hunt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_scanner.py -v`
Expected: FAIL, `scanner` not found

- [ ] **Step 3: Write minimal implementation**

`scan_spectrum` concatenates `hits_from_spectrum` for each hop and `sort_hits`s the result. If two hops report hits within 50 kHz, keep the stronger. Document in the module docstring that hops overlap by 20 percent because tuner filters roll off at the edges.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_scanner.py test_hits.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scanner.py test_scanner.py
git commit -m "Plan overlapping hops and merge hit lists."
```

---

### Task 5: HTTP service on a fake dongle

**Files:**
- Create: `radio_service.py`
- Test: `test_radio_service.py`

**Interfaces:**
- Consumes: `bands.BANDS`, `bands.classify`, `bands.listen_mode`, `scanner.hop_centers`, `scanner.scan_spectrum`, `demod.demodulate`, `hits.Hit`
- Produces:
  - `class RadioService` with `start(host='127.0.0.1', port=8766)`, `stop()`, `sweep(start_hz, stop_hz) -> dict`, `listen(freq_hz, mode) -> None` raising `PalmettoRefused` before any demod call.
  - HTTP routes from the spec. JSON keys: `freq_hz`, `power_db`, `bandwidth_hz`, `label`, `reason`, `is_new`.
  - `TODO_MARKERS: tuple[str, ...]` eight strings the file also contains as comments, so the test can read the source.

The service accepts a `reader` callable `(center_hz) -> np.ndarray` complex IQ instead of opening `RtlSdr` when `reader` is passed. Tests pass that callable. Hardware `connect` stays behind `if reader is None`.

- [ ] **Step 1: Write the failing test**

```python
import json
from urllib.request import urlopen, Request
from radio_service import RadioService, PalmettoRefused

def test_palmetto_does_not_demodulate():
    calls = []
    svc = RadioService(reader=lambda _hz: None, demod=lambda *a, **k: calls.append(1))
    try:
        svc.listen(852.35e6, "nfm")
        assert False, "expected PalmettoRefused"
    except PalmettoRefused:
        pass
    assert calls == []

def test_partial_range_rejected(http_service):
    # http_service is a fixture that binds port 0 and yields the base URL
    try:
        urlopen(http_service + "/api/sweep?start=118")
        assert False
    except Exception as exc:
        assert "400" in str(exc)

def test_sweep_json_has_reason(http_service):
    raw = urlopen(http_service + "/api/sweep?start=144&stop=146").read()
    body = json.loads(raw)
    assert body["hits"][0]["reason"] == "hunt"
    assert body["hits"][0]["is_new"] is False
    assert isinstance(body["hits"][0]["freq_hz"], float)
```

The fixture's reader returns a tone at 144.390 MHz strong enough to pass `hits_from_spectrum`, and silence elsewhere. Build that IQ the same way `AdvancedSweeper.analyze_with_fft` expects: complex samples at 2.4e6, length >= 2048, center as requested.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_radio_service.py -v`
Expected: FAIL, `radio_service` not found

- [ ] **Step 3: Write minimal implementation**

HTTP GET/POST as specified. `GET /api/health` returns `{"device": <index>, "open": true|false, "error": ""}`. `POST /api/listen` for palmetto returns 409 `{"label": "palmetto800"}`. Missing one of start/stop returns 400. Owner work runs on a single `queue.Queue` thread even when `reader` is injected, so the HTTP thread never reads samples. Put the eight TODO comments from the spec in the module. `/api/audio` sends one JSON text frame `{"rate": 48000, "channels": 1, "format": "s16le"}` then binary PCM. A one-frame test reads that JSON.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_radio_service.py test_hits.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add radio_service.py test_radio_service.py
git commit -m "Serve sweep and listen on localhost."
```

---

### Task 6: CLI, launcher, docs

**Files:**
- Create: `radio.py`
- Create: `run_radio.sh`
- Modify: `README.md`, `QUICKSTART.md`
- Test: `test_radio_cli.py`

**Interfaces:**
- Consumes: HTTP API from Task 5. No USB imports in `radio.py`.
- Produces: `stop_listen(base_url: str) -> None`, `main(argv: list[str]) -> int`, executable `run_radio.sh`.

- [ ] **Step 1: Write the failing test**

```python
from radio import format_hits, stop_listen

def test_format_orders_reasons():
    text = format_hits([
        {"freq_hz": 100e6, "power_db": -40, "reason": "active", "label": "fm_broadcast", "bandwidth_hz": 180e3},
        {"freq_hz": 144.39e6, "power_db": -80, "reason": "hunt", "label": "nfm_voice", "bandwidth_hz": 15e3},
    ])
    assert text.index("hunt") < text.index("active")

def test_stop_listen_posts(monkeypatch):
    seen = {}
    def fake(url, data=None, method=None):
        seen["url"] = url
        seen["method"] = method
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b""
        return R()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    stop_listen("http://127.0.0.1:8766")
    assert seen["url"].endswith("/api/listen/stop")
    assert seen["method"] == "POST"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_radio_cli.py -v`
Expected: FAIL, `radio` not found

- [ ] **Step 3: Write minimal implementation**

`radio.py` subcommands `scan`, `listen`, `play`. No args starts nothing in tests; `main([])` is reserved for the script path that execs the service only from `run_radio.sh` via `radio_service`. Default band `activity`. `play` reads stdin indexes. `listen` prints freq, bandwidth, label, then plays. Palmetto prints the SDRTrunk path `/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk` and returns exit code 2. `run_radio.sh` exports `LD_LIBRARY_PATH`, `chmod +x`. README quickstart and QUICKSTART gain a "Radio backend" section with the three commands and the interesting-hit reasons in one short paragraph. Do not remove the existing viewer docs.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests PASS, including the previous sweeper suite

- [ ] **Step 5: Commit**

```bash
git add radio.py run_radio.sh test_radio_cli.py README.md QUICKSTART.md
git commit -m "Add a CLI that scans for interesting hits and listens."
```

---

## Self-review

Spec coverage: bands, interesting-hit rules, demod, hops, API routes, 409, CLI, docs, TODOs, no viewer edits. Dual-dongle, UI, JMBE, SDRTrunk control, and `is_new` detection stay comments only.

Placeholder scan: task bodies name the functions and the tests. WebSocket library choice is decided above (`websockets` if the stdlib path is not shorter; Task 5 must still speak the JSON-then-s16le contract).

Type consistency: `Hit.reason` strings match the JSON `reason` field and `format_hits` keys.
