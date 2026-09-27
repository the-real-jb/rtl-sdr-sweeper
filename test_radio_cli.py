import builtins

from radio import ensure_sounddevice, format_hits, stop_listen


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
