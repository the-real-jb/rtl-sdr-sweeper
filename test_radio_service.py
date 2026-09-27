"""Tests for RadioService HTTP/WebSocket API (no hardware required).

Uses a synthetic IQ reader that places a strong tone at 144.390 MHz (a hunt
frequency) so sweep assertions are deterministic without opening a real dongle.
"""

from __future__ import annotations

import inspect
import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pytest
import websockets.sync.client

from radio_service import PalmettoRefused, RadioService

SAMPLE_RATE = 2.4e6
FFT_SIZE = 2048


def make_tone_reader(tone_hz: float = 144.390e6, amp: float = 10.0):
    """Return a callable(center_hz) -> complex ndarray with a strong tone at tone_hz."""

    def reader(center_hz: float) -> np.ndarray:
        n = FFT_SIZE
        t = np.arange(n) / SAMPLE_RATE
        offset = tone_hz - center_hz
        iq = amp * np.exp(1j * 2 * np.pi * offset * t)
        rng = np.random.default_rng(42)
        iq += 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
        return iq

    return reader


@pytest.fixture
def http_service():
    """Start RadioService on a random port, yield base URL, stop after test."""
    reader = make_tone_reader()
    svc = RadioService(reader=reader)
    svc.start(host="127.0.0.1", port=0)
    yield f"http://127.0.0.1:{svc.port}"
    svc.stop()


# ---------------------------------------------------------------------------
# Direct API tests (no HTTP server required)
# ---------------------------------------------------------------------------


def test_palmetto_does_not_demodulate():
    """listen() must raise PalmettoRefused before calling demod."""
    calls = []
    svc = RadioService(
        reader=lambda _hz: None,
        demod=lambda *a, **k: calls.append(1),
    )
    try:
        svc.listen(852.35e6, "nfm")
        assert False, "expected PalmettoRefused"
    except PalmettoRefused:
        pass
    assert calls == [], "demod must not be called for palmetto800"


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_partial_range_rejected(http_service):
    """GET /api/sweep with only start= and no stop= must return 400."""
    try:
        urlopen(http_service + "/api/sweep?start=118")
        assert False, "expected 400"
    except Exception as exc:
        assert "400" in str(exc)


def test_sweep_json_has_reason(http_service):
    """Sweep 144–146 MHz with a tone reader must produce a 'hunt' hit."""
    raw = urlopen(http_service + "/api/sweep?start=144&stop=146").read()
    body = json.loads(raw)
    assert len(body["hits"]) > 0, "expected at least one hit"
    hit = body["hits"][0]
    assert hit["reason"] == "hunt"
    assert hit["is_new"] is False
    assert isinstance(hit["freq_hz"], float), "freq_hz must be Python float"
    assert isinstance(hit["power_db"], float), "power_db must be Python float"
    assert isinstance(hit["bandwidth_hz"], float), "bandwidth_hz must be Python float"


def test_health_returns_json(http_service):
    """GET /api/health must return device, open, error keys."""
    raw = urlopen(http_service + "/api/health").read()
    body = json.loads(raw)
    assert "open" in body
    assert "device" in body
    assert "error" in body


def test_listen_palmetto_returns_409(http_service):
    """POST /api/listen with a palmetto800 frequency must return 409."""
    data = json.dumps({"freq_hz": 852.35e6, "mode": "nfm"}).encode()
    req = Request(
        http_service + "/api/listen",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(req)
        assert False, "expected 409"
    except HTTPError as exc:
        assert exc.code == 409
        body = json.loads(exc.read())
        assert body["label"] == "palmetto800"


def test_listen_stop_ok(http_service):
    """POST /api/listen/stop must return 200 even when not currently listening."""
    req = Request(
        http_service + "/api/listen/stop",
        data=b"",
        method="POST",
    )
    resp = urlopen(req)
    assert resp.getcode() == 200


def test_audio_sends_json_frame(http_service):
    """GET /api/audio WebSocket must send JSON text frame as the first message."""
    ws_url = http_service.replace("http://", "ws://") + "/api/audio"
    with websockets.sync.client.connect(ws_url) as ws:
        msg = ws.recv()
    data = json.loads(msg)
    assert data == {"rate": 48000, "channels": 1, "format": "s16le"}


# ---------------------------------------------------------------------------
# I1: GET /api/zoom
# ---------------------------------------------------------------------------


def test_zoom_returns_spectrum(http_service):
    """GET /api/zoom?freq_hz= must return spectrum, waterfall, peak_hz, bandwidth_hz, label."""
    raw = urlopen(http_service + "/api/zoom?freq_hz=144390000").read()
    body = json.loads(raw)
    # Required keys
    assert "spectrum_db" in body, f"missing spectrum_db: {list(body)}"
    assert "waterfall" in body
    assert "peak_hz" in body
    assert "bandwidth_hz" in body
    assert "label" in body
    # spectrum_db must be relative to peak (0 is max)
    spec = body["spectrum_db"]
    assert len(spec) > 0
    assert max(spec) == pytest.approx(0.0, abs=0.01), "spectrum_db must be relative to peak (0 at top)"
    # All spectrum_db values must be non-positive
    assert all(v <= 0.0 for v in spec), "spectrum_db values must be <= 0"
    # Floats must be plain Python floats, not numpy scalars (json.dumps would reject np.float64)
    assert isinstance(body["peak_hz"], float)
    assert isinstance(body["bandwidth_hz"], float)
    # Label must be a string
    assert isinstance(body["label"], str)
    # Waterfall must be a list of lists
    assert isinstance(body["waterfall"], list)
    assert all(isinstance(row, list) for row in body["waterfall"])


def test_zoom_missing_freq_returns_400(http_service):
    """GET /api/zoom with no freq_hz must return 400."""
    try:
        urlopen(http_service + "/api/zoom")
        assert False, "expected 400"
    except Exception as exc:
        assert "400" in str(exc)


def test_zoom_palmetto_ok(http_service):
    """GET /api/zoom on a Palmetto frequency must succeed (zoom != listen)."""
    raw = urlopen(http_service + "/api/zoom?freq_hz=852350000").read()
    body = json.loads(raw)
    assert "spectrum_db" in body
    assert body["label"] == "palmetto800"


# ---------------------------------------------------------------------------
# I2: Audio WebSocket binary frame (s16le, rate 48000, mono)
# ---------------------------------------------------------------------------


def test_audio_binary_frame_is_s16le(http_service):
    """I2: After the hello JSON, the next binary frame must contain valid s16le samples."""
    import numpy as np

    # Start a listen so the demod loop produces audio frames
    data = json.dumps({"freq_hz": 144.0e6, "mode": "nfm"}).encode()
    urlopen(Request(
        http_service + "/api/listen",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    ))

    ws_url = http_service.replace("http://", "ws://") + "/api/audio"
    with websockets.sync.client.connect(ws_url) as ws:
        # First frame: JSON hello
        hello = ws.recv()
        hello_data = json.loads(hello)
        assert hello_data == {"rate": 48000, "channels": 1, "format": "s16le"}

        # Second frame: binary PCM
        ws.socket.settimeout(5.0)
        frame = ws.recv()

    assert isinstance(frame, bytes), f"expected bytes, got {type(frame)}"
    assert len(frame) > 0, "binary frame must not be empty"
    assert len(frame) % 2 == 0, "s16le frames must have even byte count"
    samples = np.frombuffer(frame, dtype=np.int16)
    assert samples.dtype == np.int16, "samples must be int16 (s16le, mono)"
    # Rate and channels are asserted via hello_data above (48000, 1)


# ---------------------------------------------------------------------------
# Module-level TODO marker check
# ---------------------------------------------------------------------------


def test_todo_markers_in_source():
    """Every string in TODO_MARKERS must also appear literally in the source."""
    import radio_service

    src = inspect.getsource(radio_service)
    for marker in radio_service.TODO_MARKERS:
        assert marker in src, f"TODO marker missing from source: {marker!r}"


# ---------------------------------------------------------------------------
# Round-1 regression tests (C1, C2, C3)
# ---------------------------------------------------------------------------


def test_stop_while_streaming(http_service):
    """C1+I2: POST /api/listen/stop must succeed while a WebSocket is live.

    With the old code _handle_ws_audio blocked the HTTP thread; stop could
    never be accepted while the WebSocket connection was open.
    """
    # Start a listen on a non-palmetto frequency so _do_listen begins.
    data = json.dumps({"freq_hz": 144.0e6, "mode": "nfm"}).encode()
    urlopen(Request(
        http_service + "/api/listen",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    ))

    ws_url = http_service.replace("http://", "ws://") + "/api/audio"
    ws_ready = threading.Event()

    def hold_ws():
        with websockets.sync.client.connect(ws_url) as ws:
            ws.recv()          # receive JSON metadata frame
            ws_ready.set()     # signal: WebSocket is now live
            time.sleep(10.0)   # hold open much longer than the stop timeout (3 s)

    ws_thread = threading.Thread(target=hold_ws, daemon=True)
    ws_thread.start()
    ws_ready.wait(timeout=3)   # wait until WS connection is established

    # With C1 unfixed the HTTP thread is stuck in _handle_ws_audio and this
    # request can never be answered within the timeout.
    stop_resp = urlopen(
        Request(http_service + "/api/listen/stop", data=b"", method="POST"),
        timeout=3,
    )
    assert stop_resp.getcode() == 200
    ws_thread.join(timeout=3)


def test_ws_disconnect_releases_demod(http_service):
    """C2: Closing the WebSocket must eventually stop _do_listen.

    With the old code _listening was never cleared, so the owner thread stayed
    stuck in the demod loop and sweep requests got 'busy' back.
    """
    # Start a listen
    data = json.dumps({"freq_hz": 144.0e6, "mode": "nfm"}).encode()
    urlopen(Request(
        http_service + "/api/listen",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    ))

    ws_url = http_service.replace("http://", "ws://") + "/api/audio"
    with websockets.sync.client.connect(ws_url) as ws:
        ws.recv()   # consume JSON frame; exiting the `with` sends a Close frame

    time.sleep(0.3)  # allow daemon thread to clear _listening

    # Sweep must work now — not return {"error": "busy listening"}
    raw = urlopen(http_service + "/api/sweep?start=144&stop=146").read()
    body = json.loads(raw)
    assert "error" not in body, f"owner still busy after WS disconnect: {body}"
    assert "hits" in body


def test_second_listen_retunes(http_service):
    """C3: A second POST /api/listen must cancel the current and retune.

    With the old code the second listen item was silently discarded in
    _do_listen's queue drain, leaving service.listen() blocking for 30 s.
    """
    data1 = json.dumps({"freq_hz": 144.0e6, "mode": "nfm"}).encode()
    urlopen(Request(
        http_service + "/api/listen",
        data=data1,
        headers={"Content-Type": "application/json"},
        method="POST",
    ))

    data2 = json.dumps({"freq_hz": 145.0e6, "mode": "nfm"}).encode()
    # With C3 unfixed this blocks up to 30 s and the timeout raises URLError.
    r2 = urlopen(
        Request(
            http_service + "/api/listen",
            data=data2,
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=5,
    )
    assert r2.getcode() == 200
