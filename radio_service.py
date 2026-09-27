"""Dongle owner thread, HTTP server, and WebSocket audio endpoint.

This module is the single process that owns the SDR dongle. The HTTP handler
never reads IQ samples directly; it posts work items to the owner queue and
waits for results. That keeps libusb calls on one thread (required by pyrtlsdr).

Binding is 127.0.0.1 only — never exposed to the network.
HTTPServer (not ThreadingHTTPServer) is used deliberately so the HTTP thread
remains single-owner; the worker thread is the sole IQ reader.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import queue
import select
import socket
import struct
import threading
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np

from bands import BANDS, classify
from hits import Hit
from scanner import hop_centers, scan_spectrum
from demod import demodulate as _default_demodulate

# ---------------------------------------------------------------------------
# TODO comments — planned follow-on work. These are backlog items, not stubs.
# ---------------------------------------------------------------------------
# TODO SDRTrunk: detect running instance, avoid its USB index, list recordings
# TODO P25/DMR: add clear P25 CAI and DMR demodulators; do not read encryption keys
# TODO JMBE: call ~/SDRTrunk/jmbe vocoder for clear P25 IMBE frames
# TODO dual-dongle: run FFT sweep on device 0 while device 1 plays audio
# TODO peak-log: append timestamp, Hz, dBm, label; alert when is_new flips
# TODO max-hold: keep per-bin maximum across several passes for short transmissions
# TODO stereo-wfm: decode 19 kHz pilot and L-R baseband; first build is mono
# TODO finer-names: map FM hit to RDS channel, ham hit to calling frequency

TODO_MARKERS: tuple[str, ...] = (
    "TODO SDRTrunk: detect running instance, avoid its USB index, list recordings",
    "TODO P25/DMR: add clear P25 CAI and DMR demodulators; do not read encryption keys",
    "TODO JMBE: call ~/SDRTrunk/jmbe vocoder for clear P25 IMBE frames",
    "TODO dual-dongle: run FFT sweep on device 0 while device 1 plays audio",
    "TODO peak-log: append timestamp, Hz, dBm, label; alert when is_new flips",
    "TODO max-hold: keep per-bin maximum across several passes for short transmissions",
    "TODO stereo-wfm: decode 19 kHz pilot and L-R baseband; first build is mono",
    "TODO finer-names: map FM hit to RDS channel, ham hit to calling frequency",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLE_RATE: float = 2.4e6
_FFT_SIZE: int = 2048
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_AUDIO_INFO: dict = {"rate": 48000, "channels": 1, "format": "s16le"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PalmettoRefused(Exception):
    """Raised by listen() when freq_hz maps to palmetto800.

    Palmetto 800 is a P25 CAI trunked system (RadioReference 5042).
    Analog NFM demodulation on those carriers produces no speech.
    SDRTrunk is the correct player for talkgroups on that system.
    """


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _iq_to_spectrum(
    iq: np.ndarray,
    center_hz: float,
    sample_rate: float = _SAMPLE_RATE,
    n: int = _FFT_SIZE,
) -> tuple[np.ndarray, np.ndarray]:
    """FFT one hop of IQ into (freq_hz_array, power_db_array).

    Hann window + coherent-gain correction keeps tone amplitude consistent.
    fftshift puts bins in -Fs/2 … +Fs/2 order so DC is in the middle — that
    is where the RTL-SDR DC artefact lives, and hits.py rejects it.
    """
    if len(iq) < n:
        return np.array([]), np.array([])
    window = np.hanning(n)
    gain = float(np.mean(window))  # coherent gain ≈ 0.5 for Hann
    windowed = iq[:n] * window
    spectrum = np.fft.fftshift(np.fft.fft(windowed))
    magnitude = np.abs(spectrum) / (n * gain)
    power_db = 20.0 * np.log10(magnitude + 1e-10)
    offsets = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / sample_rate))
    freq_hz = offsets + center_hz
    return freq_hz, power_db


def _hit_to_dict(hit: Hit) -> dict:
    """Convert a Hit to a JSON-safe dict.

    Explicitly casts to Python float so the response never contains numpy
    scalar types, which json.dumps would reject.
    is_new is always False in this build — no baseline exists yet.
    """
    return {
        "freq_hz": float(hit.freq_hz),
        "power_db": float(hit.power_db),
        "bandwidth_hz": float(hit.bandwidth_hz),
        "label": hit.label,
        "reason": hit.reason,
        "is_new": False,
    }


# ---------------------------------------------------------------------------
# WebSocket frame helpers (RFC 6455, server-to-client, unmasked)
# ---------------------------------------------------------------------------


def _ws_send_frame(wfile, opcode: int, data: bytes) -> None:
    """Write one unmasked WebSocket frame to wfile."""
    length = len(data)
    header = bytes([0x80 | opcode])
    if length <= 125:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack(">H", length)
    else:
        header += bytes([127]) + struct.pack(">Q", length)
    wfile.write(header + data)
    wfile.flush()


def _ws_send_text(wfile, text: str) -> None:
    """Send a WebSocket text frame (opcode 0x01)."""
    _ws_send_frame(wfile, 0x01, text.encode("utf-8"))


def _ws_send_binary(wfile, data: bytes) -> None:
    """Send a WebSocket binary frame (opcode 0x02)."""
    _ws_send_frame(wfile, 0x02, data)


# ---------------------------------------------------------------------------
# Custom HTTPServer that lets WebSocket connections outlive their handler
# ---------------------------------------------------------------------------


class _RadioHTTPServer(http.server.HTTPServer):
    """HTTPServer that skips shutdown_request for registered WebSocket sockets.

    After _handle_ws_audio sends the 101 response it registers the raw socket
    here and returns immediately (C1 fix). serve_forever is then free to accept
    the next HTTP request. The WebSocket daemon thread calls shutdown_request
    itself when it is done.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ws_sockets: set = set()

    def shutdown_request(self, request) -> None:  # type: ignore[override]
        if request in self._ws_sockets:
            # Daemon thread owns this socket; do not shut it down here.
            return
        super().shutdown_request(request)


# ---------------------------------------------------------------------------
# RadioService
# ---------------------------------------------------------------------------


class RadioService:
    """Own the dongle thread and serve the HTTP + WebSocket API on localhost.

    Pass ``reader`` to inject a callable(center_hz) -> np.ndarray for tests.
    Hardware RtlSdr stays behind the ``reader is None`` branch and is never
    opened in tests.

    Pass ``demod`` to override the demodulator (also for tests).

    All IQ reads happen on the owner thread (_owner_loop). The HTTP handler
    posts work items to _work_queue and waits on a per-request result queue.
    """

    def __init__(
        self,
        reader: Optional[Callable] = None,
        demod: Optional[Callable] = None,
        device_index: int = 0,
    ) -> None:
        self._reader_fn = reader
        self._demod_fn = demod if demod is not None else _default_demodulate
        self._device_index = device_index
        self._work_queue: queue.Queue = queue.Queue()
        self._audio_queue: queue.Queue = queue.Queue(maxsize=16)
        self._stop_event = threading.Event()
        self._listening = False
        self._device_open = False
        self._device_error = ""
        self._httpd: Optional[_RadioHTTPServer] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._http_thread: Optional[threading.Thread] = None
        self.port: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, host: str = "127.0.0.1", port: int = 8766) -> None:
        """Start the owner thread and HTTP server. Binds 127.0.0.1 only."""
        self._stop_event.clear()
        handler_class = self._make_handler()
        # _RadioHTTPServer (not ThreadingHTTPServer) — one thread owns requests.
        self._httpd = _RadioHTTPServer((host, port), handler_class)
        # Port 0 → OS picks; store the actual bound port.
        self.port = self._httpd.server_address[1]

        self._worker_thread = threading.Thread(
            target=self._owner_loop, daemon=True, name="radio-owner"
        )
        self._worker_thread.start()

        self._http_thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="radio-http"
        )
        self._http_thread.start()

    def stop(self) -> None:
        """Shut down the HTTP server and signal the owner thread to exit."""
        self._stop_event.set()
        if self._httpd:
            self._httpd.shutdown()

    # ------------------------------------------------------------------
    # Public methods (callable without an HTTP hop, e.g. from tests)
    # ------------------------------------------------------------------

    def sweep(self, start_hz: float, stop_hz: float) -> dict:
        """Sweep start_hz–stop_hz and return a hits dict.

        Posts work to the owner thread (the only IQ reader) and blocks until
        results are ready. Floats in the returned dict are Python float.
        """
        result_q: queue.Queue = queue.Queue()
        self._work_queue.put(("sweep", start_hz, stop_hz, result_q))
        return result_q.get(timeout=30)

    def zoom(self, freq_hz: float, passes: int = 3) -> dict:
        """Retune to freq_hz and return a short spectrum + waterfall for the UI.

        Posts work to the owner thread (the only IQ reader) and blocks until
        results are ready. All floats in the returned dict are Python float.

        Returns:
            spectrum_db: list[float] — power in dB relative to the peak (0 = max)
            waterfall:   list[list[float]] — one row per pass, same normalization
            peak_hz:     float — frequency of the peak bin
            bandwidth_hz:float — contiguous −6 dB bandwidth around the peak
            label:       str — band label from classify()
        """
        result_q: queue.Queue = queue.Queue()
        self._work_queue.put(("zoom", freq_hz, passes, result_q))
        return result_q.get(timeout=30)

    def listen(self, freq_hz: float, mode: str) -> None:
        """Tune to freq_hz and demodulate.

        Raises PalmettoRefused immediately — before any demod call — when
        freq_hz is in the Palmetto 800 downlink range.
        Palmetto 800 uses P25 CAI; analog NFM produces no intelligible audio.
        """
        label = classify(freq_hz)
        if label == "palmetto800":
            # Raise before touching the demodulator or work queue.
            raise PalmettoRefused(label)
        result_q: queue.Queue = queue.Queue()
        self._work_queue.put(("listen", freq_hz, mode, result_q))
        result_q.get(timeout=30)

    # ------------------------------------------------------------------
    # Owner thread — the only thread that touches the reader / dongle
    # ------------------------------------------------------------------

    def _owner_loop(self) -> None:
        """IQ reads happen only here. HTTP handler posts work; we execute it."""
        if self._reader_fn is None:
            # TODO SDRTrunk: detect running instance, avoid its USB index, list recordings
            self._connect_hardware()

        while not self._stop_event.is_set():
            try:
                item = self._work_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            kind = item[0]

            if kind == "sweep":
                _, start_hz, stop_hz, result_q = item
                try:
                    result = self._do_sweep(start_hz, stop_hz)
                except Exception as exc:
                    result = {"error": str(exc), "hits": []}
                result_q.put(result)

            elif kind == "listen":
                _, freq_hz, mode, result_q = item
                # ACK immediately so the HTTP handler can return 200 without
                # waiting for the demod loop to finish.
                result_q.put({"ok": True})
                # Run demod, then retune if _do_listen says so (C3 fix).
                retune = self._do_listen(freq_hz, mode)
                while retune[0] is not None:
                    retune = self._do_listen(*retune)

            elif kind == "zoom":
                _, freq_hz, passes, result_q = item
                try:
                    result = self._do_zoom(freq_hz, passes)
                except Exception as exc:
                    result = {"error": str(exc)}
                result_q.put(result)

            elif kind == "stop_listen":
                # TODO peak-log: append timestamp, Hz, dBm, label; alert when is_new flips
                _, result_q = item
                self._listening = False
                result_q.put({"ok": True})

    def _do_zoom(self, freq_hz: float, passes: int = 3) -> dict:
        """Read 'passes' IQ blocks centred on freq_hz and build the zoom response.

        All power values are normalised so the global peak = 0 dB (relative).
        The -6 dB bandwidth is the contiguous range of bins within 6 dB of peak.
        Palmetto frequencies are allowed through (zoom ≠ listen).
        """
        label = classify(freq_hz)

        spectra_db: list[np.ndarray] = []
        freqs: np.ndarray | None = None

        for _ in range(max(1, passes)):
            iq = self._reader(freq_hz)
            if iq is None:
                break
            iq = np.asarray(iq)
            if iq.size == 0:
                break
            f, p = _iq_to_spectrum(iq, freq_hz)
            if f.size == 0:
                break
            if freqs is None:
                freqs = f
            spectra_db.append(p)

        if not spectra_db or freqs is None:
            return {
                "spectrum_db": [],
                "waterfall": [],
                "peak_hz": float(freq_hz),
                "bandwidth_hz": 0.0,
                "label": label,
            }

        # Global peak across all passes — normalise everything to this.
        global_peak = float(max(p.max() for p in spectra_db))
        norm = [p - global_peak for p in spectra_db]

        first = norm[0]
        peak_idx = int(np.argmax(first))
        peak_hz = float(freqs[peak_idx])

        # Contiguous -6 dB bandwidth around the peak bin.
        left = peak_idx
        while left > 0 and first[left - 1] >= -6.0:
            left -= 1
        right = peak_idx
        while right < len(first) - 1 and first[right + 1] >= -6.0:
            right += 1
        if right > left:
            bandwidth_hz = float(freqs[right] - freqs[left])
        else:
            bin_width = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
            bandwidth_hz = bin_width

        return {
            "spectrum_db": [float(v) for v in first],
            "waterfall": [[float(v) for v in row] for row in norm],
            "peak_hz": peak_hz,
            "bandwidth_hz": bandwidth_hz,
            "label": label,
        }

    def _connect_hardware(self) -> None:
        """Open the RtlSdr dongle. Only called when reader is None (live hardware)."""
        try:
            from rtlsdr import RtlSdr  # type: ignore
            self._sdr = RtlSdr(self._device_index)
            self._sdr.sample_rate = _SAMPLE_RATE
            self._device_open = True
        except Exception as exc:
            self._device_error = str(exc)
            self._device_open = False

    def _reader(self, center_hz: float) -> np.ndarray:
        """Read one hop of IQ samples. Must only be called from the owner thread."""
        if self._reader_fn is not None:
            return self._reader_fn(center_hz)
        self._sdr.center_freq = center_hz
        return self._sdr.read_samples(_FFT_SIZE)

    def _do_sweep(self, start_hz: float, stop_hz: float) -> dict:
        """Run the multi-hop sweep on the owner thread."""
        # Hop spacing equals the sample rate — one full bandwidth per hop.
        centers = hop_centers(start_hz, stop_hz, _SAMPLE_RATE)
        sweep_data = []
        for center in centers:
            iq = self._reader(center)
            if iq is None:
                continue
            iq = np.asarray(iq)
            if len(iq) < _FFT_SIZE:
                continue
            freq_hz, power_db = _iq_to_spectrum(iq, center)
            if freq_hz.size > 0:
                sweep_data.append((freq_hz, power_db, center))
        hits = scan_spectrum(sweep_data)
        return {"hits": [_hit_to_dict(h) for h in hits]}

    def _do_listen(
        self, freq_hz: float, mode: str
    ) -> tuple[Optional[float], Optional[str]]:
        """Demodulate on the owner thread, push PCM to the audio queue.

        Returns (new_freq, new_mode) if a second listen request arrived while
        this one was running (C3: second listen cancels and retunes), or
        (None, None) if stopped cleanly.

        The queue drain at the top of each iteration lets stop_listen and a
        second listen interrupt the loop promptly without blocking the owner
        thread on a long read.
        """
        # TODO dual-dongle: run FFT sweep on device 0 while device 1 plays audio
        self._listening = True
        _retune: tuple[Optional[float], Optional[str]] = (None, None)

        while self._listening and not self._stop_event.is_set():
            # Drain any pending work before reading IQ (keeps stop latency low).
            try:
                item = self._work_queue.get_nowait()
                if item[0] == "stop_listen":
                    item[1].put({"ok": True})
                    self._listening = False
                    break
                elif item[0] == "listen":
                    # C3: a second listen arrived — cancel this one, retune.
                    _, new_freq, new_mode, result_q = item
                    result_q.put({"ok": True})
                    self._listening = False
                    _retune = (new_freq, new_mode)
                    break
                elif item[0] == "sweep":
                    item[3].put({"error": "busy listening", "hits": []})
            except queue.Empty:
                pass

            if not self._listening:
                break

            iq = self._reader(freq_hz)
            if iq is None:
                break
            iq = np.asarray(iq)
            if iq.size == 0:
                break
            # TODO max-hold: keep per-bin maximum across several passes for short transmissions
            # TODO stereo-wfm: decode 19 kHz pilot and L-R baseband; first build is mono
            pcm = self._demod_fn(iq, _SAMPLE_RATE, mode)
            try:
                self._audio_queue.put_nowait(pcm)
            except queue.Full:
                pass

        return _retune

    # ------------------------------------------------------------------
    # HTTP handler factory
    # ------------------------------------------------------------------

    def _make_handler(self) -> type:
        """Return a BaseHTTPRequestHandler subclass that closes over this service."""
        service = self

        class _Handler(http.server.BaseHTTPRequestHandler):

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                params = parse_qs(parsed.query)

                if path == "/api/health":
                    self._send_json(
                        200,
                        {
                            "device": service._device_index,
                            "open": service._device_open,
                            "error": service._device_error,
                        },
                    )

                elif path == "/api/sweep":
                    start_val = params.get("start", [None])[0]
                    stop_val = params.get("stop", [None])[0]
                    if start_val is None and stop_val is None:
                        # Default band is 'activity' (118–174 MHz) when both omitted.
                        start_hz, stop_hz = BANDS["activity"]
                    elif start_val is None or stop_val is None:
                        # Partial range — caller must provide both or neither.
                        self._send_json(
                            400, {"error": "both start and stop required (MHz)"}
                        )
                        return
                    else:
                        start_hz = float(start_val) * 1e6
                        stop_hz = float(stop_val) * 1e6
                    result = service.sweep(start_hz, stop_hz)
                    self._send_json(200, result)

                elif path == "/api/zoom":
                    freq_val = params.get("freq_hz", [None])[0]
                    if freq_val is None:
                        self._send_json(400, {"error": "freq_hz is required"})
                        return
                    result = service.zoom(float(freq_val))
                    self._send_json(200, result)

                elif path == "/api/audio":
                    upgrade = self.headers.get("Upgrade", "").lower()
                    if upgrade != "websocket":
                        self._send_json(400, {"error": "WebSocket upgrade required"})
                        return
                    self._handle_ws_audio()

                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/api/listen":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    freq_hz = float(body.get("freq_hz", 0))
                    mode = body.get("mode", "")
                    # I3: single palmetto check — PalmettoRefused from service.listen()
                    # is the authoritative path; no redundant classify call here.
                    try:
                        service.listen(freq_hz, mode)
                        self._send_json(200, {"ok": True})
                    except PalmettoRefused:
                        # TODO P25/DMR: add clear P25 CAI and DMR demodulators; do not read encryption keys
                        # Palmetto 800 is P25 CAI — NFM demod makes no speech here.
                        self._send_json(409, {"label": "palmetto800"})

                elif path == "/api/listen/stop":
                    result_q: queue.Queue = queue.Queue()
                    service._work_queue.put(("stop_listen", result_q))
                    result_q.get(timeout=5)
                    self._send_json(200, {"ok": True})

                else:
                    self._send_json(404, {"error": "not found"})

            def _send_json(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle_ws_audio(self) -> None:
                """Upgrade to WebSocket and hand off to a daemon send-thread.

                C1 fix: after sending 101 and registering the socket in
                _ws_sockets, this method returns immediately so serve_forever
                can accept the next HTTP request (e.g. POST /api/listen/stop).

                The daemon thread owns the socket for its lifetime:
                  - Sends the JSON metadata text frame first.
                  - Streams binary PCM s16le frames from the audio queue.
                  - On client close or OSError, clears service._listening (C2+I1)
                    and calls server.shutdown_request to clean up the socket.

                GET /api/audio is the contract the CLI and later UI will use.
                Do not rename or move the route.
                """
                key = self.headers.get("Sec-WebSocket-Key", "")
                accept = base64.b64encode(
                    hashlib.sha1((key + _WS_MAGIC).encode()).digest()
                ).decode()
                # Write the 101 response directly so we control every header byte.
                upgrade_resp = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    "\r\n"
                )
                self.wfile.write(upgrade_resp.encode())
                self.wfile.flush()

                # Register socket so _RadioHTTPServer skips shutdown_request.
                conn = self.connection
                self.server._ws_sockets.add(conn)

                # Create a separate write file for the daemon thread.
                # makefile() increments _io_refs so the socket stays alive after
                # finish() closes the handler's wfile/rfile.
                ws_wfile = conn.makefile("wb", buffering=0)

                def ws_sender(conn=conn, ws_wfile=ws_wfile) -> None:
                    """Send PCM over WebSocket; runs on its own daemon thread."""
                    try:
                        # First frame: audio format metadata (text).
                        _ws_send_text(ws_wfile, json.dumps(_AUDIO_INFO))

                        # Stream PCM frames until client closes or service stops.
                        while not service._stop_event.is_set():
                            # Poll for incoming frames (close frame from client).
                            r, _, _ = select.select([conn], [], [], 0.05)
                            if r:
                                try:
                                    data = conn.recv(4096)
                                except OSError:
                                    break
                                if not data or (data[0] & 0x0F) == 0x08:
                                    # Echo WebSocket close frame to complete handshake.
                                    try:
                                        _ws_send_frame(ws_wfile, 0x08, b"")
                                    except OSError:
                                        pass
                                    break

                            # Send available PCM.
                            try:
                                pcm = service._audio_queue.get_nowait()
                                _ws_send_binary(ws_wfile, pcm.tobytes())
                            except queue.Empty:
                                pass

                    except OSError:
                        pass  # Unclean drop; fall through to finally.
                    finally:
                        # C2+I1: client gone — stop the demod loop so the dongle
                        # is released. A 2-second grace period could be added here
                        # to ride out brief reconnects; skipped in this build.
                        service._listening = False
                        service._httpd._ws_sockets.discard(conn)
                        try:
                            ws_wfile.close()
                        except OSError:
                            pass
                        try:
                            # Full close: shut down both directions then release FD.
                            conn.shutdown(socket.SHUT_RDWR)
                            conn.close()
                        except OSError:
                            pass

                t = threading.Thread(
                    target=ws_sender, daemon=True, name="radio-ws"
                )
                t.start()
                # Return immediately. serve_forever is now free for the next
                # HTTP request. shutdown_request is skipped by _RadioHTTPServer
                # until ws_sender cleans up (C1 fix).

            def log_message(self, *args: object) -> None:  # noqa: D401
                pass  # Suppress HTTP access logs during tests.

        return _Handler
