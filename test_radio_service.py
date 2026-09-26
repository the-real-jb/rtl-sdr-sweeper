"""Tests for RadioService HTTP/WebSocket API (no hardware required).

Uses a synthetic IQ reader that places a strong tone at 144.390 MHz (a hunt
frequency) so sweep assertions are deterministic without opening a real dongle.
"""

from __future__ import annotations

import inspect
import json
from urllib.error import HTTPError
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
# Module-level TODO marker check
# ---------------------------------------------------------------------------


def test_todo_markers_in_source():
    """Every string in TODO_MARKERS must also appear literally in the source."""
    import radio_service

    src = inspect.getsource(radio_service)
    for marker in radio_service.TODO_MARKERS:
        assert marker in src, f"TODO marker missing from source: {marker!r}"
