import builtins
import json

from radio import (
    _build_parser,
    _is_chatter_candidate,
    _pick_candidate,
    ensure_sounddevice,
    format_hits,
    run_monitor,
    stop_listen,
)
from tracker import OccupancyEvent, OccupancyTracker


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
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    stop_listen("http://127.0.0.1:8766")
    assert seen["url"].endswith("/api/listen/stop")
    assert seen["method"] == "POST"


def test_ensure_sounddevice_missing_hint(capsys, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sounddevice":
            raise ModuleNotFoundError("No module named 'sounddevice'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    code = ensure_sounddevice()
    captured = capsys.readouterr()
    assert code == 1
    assert "uv pip install sounddevice" in captured.err


# ---------------------------------------------------------------------------
# C1: --device and --port must be accepted after the subcommand name
# ---------------------------------------------------------------------------

def test_device_after_play_subcommand():
    """play --band fm --device 1 must parse device=1 (C1)."""
    parser = _build_parser()
    args = parser.parse_args(["play", "--band", "fm", "--device", "1"])
    assert args.command == "play"
    assert args.band == "fm"
    assert args.device == 1


def test_device_after_scan_subcommand():
    """scan --band fm --device 1 must parse device=1 (C1)."""
    parser = _build_parser()
    args = parser.parse_args(["scan", "--band", "fm", "--device", "1"])
    assert args.command == "scan"
    assert args.device == 1


def test_device_after_listen_subcommand():
    """listen --freq 88.5 --device 1 must parse device=1 (C1)."""
    parser = _build_parser()
    args = parser.parse_args(["listen", "--freq", "88.5", "--device", "1"])
    assert args.command == "listen"
    assert args.device == 1


def test_port_after_play_subcommand():
    """play --band fm --port 9000 must parse port=9000 (C1)."""
    parser = _build_parser()
    args = parser.parse_args(["play", "--band", "fm", "--port", "9000"])
    assert args.port == 9000


# ---------------------------------------------------------------------------
# monitor CLI wiring
# ---------------------------------------------------------------------------

def test_monitor_subcommand_parses_device_port():
    parser = _build_parser()
    args = parser.parse_args(["monitor", "--device", "1", "--port", "9000"])
    assert args.command == "monitor"
    assert args.device == 1
    assert args.port == 9000


def test_pick_candidate_skips_palmetto():
    ranked = [
        OccupancyEvent(
            freq_hz=770e6,
            power_db=-40,
            bandwidth_hz=12e3,
            label="palmetto800",
            reason="active",
            is_new=False,
            duty=0.5,
            kind="burst",
        ),
        OccupancyEvent(
            freq_hz=146.52e6,
            power_db=-70,
            bandwidth_hz=12e3,
            label="nfm_voice",
            reason="voice",
            is_new=True,
            duty=0.4,
            kind="burst",
        ),
    ]
    pick = _pick_candidate(ranked)
    assert pick is not None
    assert pick.label == "nfm_voice"


def test_is_chatter_skips_persistent_fm():
    sticky = OccupancyEvent(
        freq_hz=100.1e6,
        power_db=-40,
        bandwidth_hz=180e3,
        label="fm_broadcast",
        reason="active",
        is_new=False,
        duty=1.0,
        kind="persistent",
    )
    assert _is_chatter_candidate(sticky) is False


def test_monitor_loop_listens_to_ranked_candidate(monkeypatch, capsys):
    """One band cycle: sweep → rank → POST listen → stop (mocked HTTP/audio)."""
    calls: list[str] = []

    def fake_fetch(url, *, data=None, method=None):
        if "/api/sweep" in url:
            calls.append("sweep")
            return {
                "hits": [
                    {
                        "freq_hz": 146.52e6,
                        "power_db": -70.0,
                        "bandwidth_hz": 12_500.0,
                        "label": "nfm_voice",
                        "reason": "voice",
                        "is_new": False,
                    }
                ]
            }
        if url.endswith("/api/listen") and method == "POST":
            calls.append("listen")
            body = json.loads(data.decode())
            assert body["freq_hz"] == 146.52e6
            assert body["mode"] == "nfm"
            return {}
        if "/api/zoom" in url:
            calls.append("zoom")
            return {"peak_hz": 146.52e6, "bandwidth_hz": 0.0, "label": "nfm_voice"}
        raise AssertionError(f"unexpected url {url}")

    def fake_stop(base_url):
        calls.append("stop")

    monkeypatch.setattr("radio._fetch_json", fake_fetch)
    monkeypatch.setattr("radio.stop_listen", fake_stop)
    monkeypatch.setattr("radio.ensure_sounddevice", lambda: 0)
    monkeypatch.setattr("radio._play_audio_for", lambda *a, **k: None)

    bands = [("ham2m", 144e6, 148e6)]
    code = run_monitor(
        "http://127.0.0.1:8766",
        bands=bands,
        max_cycles=1,
        max_dwell_s=0.01,
        quiet_check_s=0.01,
        tracker=OccupancyTracker(window=5, min_history=1),
    )
    assert code == 0
    assert "listen" in calls
    assert "stop" in calls
    out = capsys.readouterr().out
    assert "146.5200" in out
    assert "nfm_voice" in out


def test_monitor_skips_palmetto_no_listen(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, *, data=None, method=None):
        if "/api/sweep" in url:
            calls.append("sweep")
            return {
                "hits": [
                    {
                        "freq_hz": 852.35e6,
                        "power_db": -50.0,
                        "bandwidth_hz": 12_500.0,
                        "label": "palmetto800",
                        "reason": "active",
                        "is_new": False,
                    }
                ]
            }
        if url.endswith("/api/listen"):
            calls.append("listen")
            return {}
        return {}

    monkeypatch.setattr("radio._fetch_json", fake_fetch)
    monkeypatch.setattr("radio.stop_listen", lambda *_: None)
    monkeypatch.setattr("radio.ensure_sounddevice", lambda: 0)
    monkeypatch.setattr("radio._play_audio_for", lambda *a, **k: None)

    code = run_monitor(
        "http://127.0.0.1:8766",
        bands=[("palmetto", 851e6, 861e6)],
        max_cycles=1,
        max_dwell_s=0.01,
        quiet_check_s=0.01,
        tracker=OccupancyTracker(window=5, min_history=1),
    )
    assert code == 0
    assert "sweep" in calls
    assert "listen" not in calls
