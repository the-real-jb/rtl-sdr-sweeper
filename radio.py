"""CLI client for the radio backend HTTP API.

Owns scan/listen/play commands, hit formatting, and speaker playback via
sounddevice. Never opens a dongle — all RF work stays in radio_service.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from bands import BANDS, classify, listen_mode

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8766
_SDRTRUNK_PATH = "/home/jb/apps/sdr-trunk-linux-x86_64-v0.6.1/bin/sdr-trunk"
_REASON_ORDER = {"hunt": 0, "narrow_in_wide": 1, "voice": 2, "active": 3}
_REPO_ROOT = Path(__file__).resolve().parent


def _base_url(port: int = _DEFAULT_PORT) -> str:
    return f"http://{_DEFAULT_HOST}:{port}"


def format_hits(hits: list[dict]) -> str:
    """Format hits sorted by reason priority then strongest power."""
    ordered = sorted(
        hits,
        key=lambda h: (_REASON_ORDER.get(h["reason"], 99), -float(h["power_db"])),
    )
    lines: list[str] = []
    for i, hit in enumerate(ordered, start=1):
        mhz = float(hit["freq_hz"]) / 1e6
        lines.append(
            f"{i:3d}  {mhz:9.4f} MHz  {hit['power_db']:6.1f} dB  "
            f"{hit['bandwidth_hz']/1e3:6.1f} kHz  {hit['label']}  ({hit['reason']})"
        )
    return "\n".join(lines)


def ensure_sounddevice() -> int:
    """Return 0 if sounddevice is installed, else print install hint and return 1."""
    try:
        import sounddevice  # noqa: F401
    except ModuleNotFoundError:
        print(
            "Speaker playback requires sounddevice. Install with: uv pip install sounddevice",
            file=sys.stderr,
        )
        return 1
    return 0


def stop_listen(base_url: str) -> None:
    """POST /api/listen/stop so the service releases the tune."""
    url = f"{base_url.rstrip('/')}/api/listen/stop"
    with urllib.request.urlopen(url, data=b"", method="POST") as resp:
        resp.read()


def _fetch_json(url: str, *, data: bytes | None = None, method: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        body = exc.read().decode()
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"error": body}
        detail["_http_code"] = exc.code
        return detail


def _health(base_url: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as resp:
            return json.loads(resp.read())
    except (URLError, TimeoutError):
        return None


def _ensure_service(device: int, port: int) -> str:
    base = _base_url(port)
    if _health(base) is not None:
        return base
    launcher = _REPO_ROOT / "run_radio.sh"
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = f"{os.path.expanduser('~')}/.local/lib:{env.get('LD_LIBRARY_PATH', '')}"
    cmd = [str(launcher)]
    if device != 0:
        cmd.extend(["--device", str(device)])
    if port != _DEFAULT_PORT:
        cmd.extend(["--port", str(port)])
    subprocess.Popen(cmd, cwd=_REPO_ROOT, env=env, start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        if _health(base) is not None:
            return base
    print("Could not reach radio service on", base, file=sys.stderr)
    return base


def _check_health(base_url: str) -> int:
    health = _health(base_url)
    if health is None:
        print(f"Radio service not running at {base_url}", file=sys.stderr)
        return 1
    if not health.get("open") and health.get("error"):
        print(health["error"], file=sys.stderr)
        return 1
    return 0


def _sweep_mhz_range(args: argparse.Namespace) -> tuple[float, float] | None:
    """Return start/stop in MHz, or None to use the service default (activity)."""
    if args.start is not None or args.stop is not None:
        if args.start is None or args.stop is None:
            print("Both --start and --stop are required (MHz).", file=sys.stderr)
            raise SystemExit(1)
        return float(args.start), float(args.stop)
    if args.band:
        low, high = BANDS[args.band]
        return low / 1e6, high / 1e6
    return None


def _cmd_scan(args: argparse.Namespace) -> int:
    base = _ensure_service(args.device, args.port)
    code = _check_health(base)
    if code:
        return code
    sweep = _sweep_mhz_range(args)
    url = f"{base}/api/sweep"
    if sweep is not None:
        url = f"{url}?{urlencode({'start': sweep[0], 'stop': sweep[1]})}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        body = json.loads(resp.read())
    text = format_hits(body.get("hits", []))
    if text:
        print(text)
    else:
        print("No hits.")
    return 0


def _play_audio_ws(base_url: str) -> None:
    import numpy as np
    import sounddevice as sd
    import websockets.sync.client

    ws_url = base_url.replace("http://", "ws://") + "/api/audio"
    with websockets.sync.client.connect(ws_url) as ws:
        meta = json.loads(ws.recv())
        rate = int(meta["rate"])
        with sd.OutputStream(samplerate=rate, channels=1, dtype="int16") as stream:
            while True:
                frame = ws.recv()
                if isinstance(frame, str):
                    continue
                pcm = np.frombuffer(frame, dtype=np.int16)
                stream.write(pcm)


def _start_listen(
    base_url: str, freq_hz: float, mode: str, *, bandwidth_hz: float | None = None
) -> int:
    label = classify(freq_hz)
    if label == "palmetto800":
        print(_SDRTRUNK_PATH)
        return 2
    bw = bandwidth_hz if bandwidth_hz is not None else 0.0
    print(f"{freq_hz/1e6:.4f} MHz  {bw/1e3:.1f} kHz  {label}")
    payload = json.dumps({"freq_hz": freq_hz, "mode": mode}).encode()
    result = _fetch_json(
        f"{base_url}/api/listen",
        data=payload,
        method="POST",
    )
    if result.get("_http_code") == 409:
        print(_SDRTRUNK_PATH)
        return 2
    if ensure_sounddevice() != 0:
        stop_listen(base_url)
        return 1
    try:
        _play_audio_ws(base_url)
    except KeyboardInterrupt:
        pass
    finally:
        stop_listen(base_url)
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    base = _ensure_service(args.device, args.port)
    code = _check_health(base)
    if code:
        return code
    freq_hz = float(args.freq) * 1e6
    label = classify(freq_hz)
    mode = args.mode or listen_mode(label)
    if mode is None:
        print(_SDRTRUNK_PATH)
        return 2
    # Zoom to get the measured -6 dB bandwidth before starting audio.
    zoom = _fetch_json(f"{base}/api/zoom?{urlencode({'freq_hz': freq_hz})}")
    bw = float(zoom.get("bandwidth_hz", 0.0))
    return _start_listen(base, freq_hz, mode, bandwidth_hz=bw)


def _cmd_play(args: argparse.Namespace) -> int:
    base = _ensure_service(args.device, args.port)
    code = _check_health(base)
    if code:
        return code
    sweep = _sweep_mhz_range(args)
    url = f"{base}/api/sweep"
    if sweep is not None:
        url = f"{url}?{urlencode({'start': sweep[0], 'stop': sweep[1]})}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        body = json.loads(resp.read())
    hits = sorted(
        body.get("hits", []),
        key=lambda h: (_REASON_ORDER.get(h["reason"], 99), -float(h["power_db"])),
    )
    if not hits:
        print("No hits.")
        return 0
    print(format_hits(hits))
    while True:
        try:
            line = input("Hit number (q to quit): ").strip()
        except EOFError:
            break
        if not line or line.lower() == "q":
            break
        try:
            idx = int(line)
        except ValueError:
            continue
        if idx < 1 or idx > len(hits):
            continue
        hit = hits[idx - 1]
        mode = listen_mode(hit["label"])
        if mode is None:
            print(_SDRTRUNK_PATH)
            return 2
        rc = _start_listen(
            base,
            float(hit["freq_hz"]),
            mode,
            bandwidth_hz=float(hit.get("bandwidth_hz", 0)),
        )
        if rc:
            return rc
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radio")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan")
    scan.add_argument("--band", choices=list(BANDS.keys()))
    scan.add_argument("--start", type=float)
    scan.add_argument("--stop", type=float)
    # C1: --device/--port may appear after the subcommand name (e.g. play --band fm --device 1)
    scan.add_argument("--device", type=int, default=0)
    scan.add_argument("--port", type=int, default=_DEFAULT_PORT)

    listen = sub.add_parser("listen")
    listen.add_argument("--freq", type=float, required=True)
    listen.add_argument("--mode", choices=["wfm", "nfm", "am"])
    listen.add_argument("--device", type=int, default=0)
    listen.add_argument("--port", type=int, default=_DEFAULT_PORT)

    play = sub.add_parser("play")
    play.add_argument("--band", choices=list(BANDS.keys()))
    play.add_argument("--start", type=float)
    play.add_argument("--stop", type=float)
    play.add_argument("--device", type=int, default=0)
    play.add_argument("--port", type=int, default=_DEFAULT_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return 0
    parser = _build_parser()
    args = parser.parse_args(args_list)
    if args.command == "scan":
        return _cmd_scan(args)
    if args.command == "listen":
        return _cmd_listen(args)
    if args.command == "play":
        return _cmd_play(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
