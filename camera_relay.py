"""Bounded MJPEG relay. Standard library only: no AI models or video decoding."""

import base64
import binascii
import hmac
import json
import logging
import math
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_opener():
    # Never forward credentials through redirects or system HTTP proxies.
    return build_opener(ProxyHandler({}), NoRedirect())


def read_exact(source, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = source.read(length - len(chunks))
        if not chunk:
            raise EOFError("Stream interrotto")
        chunks.extend(chunk)
    return bytes(chunks)


def mjpeg_frames(source, content_type, max_bytes):
    message = Message()
    message["content-type"] = content_type
    boundary = message.get_param("boundary")
    if message.get_content_type() != "multipart/x-mixed-replace" or not boundary:
        raise ValueError("Content-Type MJPEG non valido")
    marker = b"--" + boundary.encode("ascii")
    if len(marker) > 202:
        raise ValueError("Boundary troppo lungo")
    while True:
        line = source.readline(4097)
        if not line:
            raise EOFError("Stream terminato")
        if line.strip() == marker + b"--":
            return
        if line.strip() != marker:
            if line.strip():
                raise ValueError("Boundary MJPEG non valido")
            continue
        headers = {}
        total = 0
        while True:
            line = source.readline(4097)
            total += len(line)
            if not line or total > 8192 or len(line) > 4096:
                raise ValueError("Header MJPEG non valido")
            if line in (b"\r\n", b"\n"):
                break
            name, value = line.decode("ascii").split(":", 1)
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if headers.get("content-type", "").lower() != "image/jpeg" or not 0 < length <= max_bytes:
            raise ValueError("Fotogramma MJPEG non valido o troppo grande")
        data = read_exact(source, length)
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise ValueError("JPEG incompleto")
        yield data


@dataclass(frozen=True)
class Frame:
    data: bytes
    received: float
    sequence: int


class FrameBuffer:
    def __init__(self, *, max_age=3.0, max_bytes=512 * 1024):
        self.frames = deque(maxlen=5)
        self.condition = threading.Condition()
        self.max_age = max_age
        self.max_bytes = max_bytes
        self.sequence = 0

    def put(self, data):
        if len(data) > self.max_bytes:
            raise ValueError("Fotogramma troppo grande")
        with self.condition:
            self.sequence += 1
            self.frames.append(Frame(data, time.monotonic(), self.sequence))
            self.condition.notify_all()

    def latest(self):
        with self.condition:
            if not self.frames or time.monotonic() - self.frames[-1].received > self.max_age:
                return None
            return self.frames[-1]


class CameraReader:
    def __init__(self, url, buffer, *, fps=5.0):
        self.url = url
        self.buffer = buffer
        self.interval = 1 / fps
        self.stop = threading.Event()

    def run(self):
        backoff = 1
        while not self.stop.is_set():
            try:
                # No COSO_API_KEY is sent to the ESPCam.
                with local_opener().open(self.url, timeout=3) as response:
                    if not 200 <= response.status < 300:
                        raise ValueError("Risposta camera non valida")
                    last = -float("inf")
                    for data in mjpeg_frames(response, response.headers.get("Content-Type", ""), self.buffer.max_bytes):
                        if self.stop.is_set():
                            return
                        now = time.monotonic()
                        if now - last >= self.interval:
                            self.buffer.put(data)
                            last = now
                            backoff = 1
            except (OSError, ValueError, EOFError, HTTPException) as exc:
                logging.warning("Camera disconnessa (%s); nuovo tentativo tra %ss", type(exc).__name__, backoff)
            self.stop.wait(backoff)
            backoff = min(backoff * 2, 15)


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, buffer, api_key, max_viewers=2, fps=5.0, *, username="", password=""):
        if bool(username) != bool(password) or (username and (not username.strip() or ":" in username)) or (password and not password.strip()):
            raise ValueError("Configura CAMERA_USERNAME e CAMERA_PASSWORD insieme; l'utente non può contenere ':'")
        self.buffer = buffer
        self.api_key = api_key
        self.video_credentials = f"{username}:{password}".encode("utf-8") if username else None
        self.viewers = threading.BoundedSemaphore(max_viewers)
        self.requests = threading.BoundedSemaphore(max_viewers + 6)
        self.interval = 1 / fps
        super().__init__(address, RelayHandler)

    def process_request(self, request, client_address):
        if not self.requests.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.requests.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.requests.release()


class RelayHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, format, *args):
        # Do not log request URLs or headers (may contain user-supplied secrets).
        pass

    def parse_request(self):
        if not super().parse_request():
            return False
        # Authenticate even HEAD, OPTIONS and unsupported HTTP methods.
        api_authenticated = hmac.compare_digest(self.headers.get("X-API-Key", "").encode(), self.server.api_key.encode())
        if not api_authenticated and not self.video_authenticated():
            headers = {}
            if self.path in {"/snapshot", "/stream"} and self.server.video_credentials:
                headers["WWW-Authenticate"] = 'Basic realm="Buddy camera", charset="UTF-8"'
            self.error(401, "unauthorized", "Credenziali mancanti o non valide.", **headers)
            return False
        return True

    def video_authenticated(self):
        # Home Assistant/browser credentials only grant read access to video.
        if self.command not in {"GET", "HEAD"} or self.path not in {"/snapshot", "/stream"} or not self.server.video_credentials:
            return False
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            provided = base64.b64decode(token, validate=True)
        except (ValueError, binascii.Error):
            return False
        return hmac.compare_digest(provided, self.server.video_credentials)

    def send_error(self, code, message=None, explain=None):
        self.error(code, "http_error", message or self.responses.get(code, ("Errore HTTP",))[0])

    def reply(self, status, data, mime, **headers):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, str(value))
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(data)

    def error(self, status, code, message, **headers):
        self.reply(status, json.dumps({"error": code, "message": message}).encode(), "application/json", **headers)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        buffer = self.server.buffer
        if self.path == "/health":
            with buffer.condition:
                count = len(buffer.frames)
            self.reply(200, json.dumps({"status": "ok", "camera_ready": buffer.latest() is not None, "buffered_frames": count}).encode(), "application/json")
            return
        if self.path not in {"/snapshot", "/stream"}:
            self.error(404, "not_found", "Endpoint non trovato.")
            return
        frame = buffer.latest()
        if frame is None:
            self.error(503, "camera_unavailable", "Nessun fotogramma recente disponibile.")
            return
        if self.path == "/snapshot":
            self.reply(200, frame.data, "image/jpeg", **{"X-Frame-Age": time.monotonic() - frame.received, "X-Frame-Sequence": frame.sequence})
            return
        if not self.server.viewers.acquire(blocking=False):
            self.error(503, "viewer_limit", "Troppi spettatori collegati.")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=cosoframe")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            sequence = 0
            while True:
                frame = buffer.latest()
                if frame is None:
                    return
                if frame.sequence != sequence:
                    self.wfile.write(b"--cosoframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame.data)).encode() + b"\r\n\r\n" + frame.data + b"\r\n")
                    self.wfile.flush()
                    sequence = frame.sequence
                time.sleep(self.server.interval)
        except (OSError, socket.timeout):
            pass
        finally:
            self.server.viewers.release()


def main():
    key = os.environ.get("COSO_API_KEY", "")
    if not key.strip():
        raise RuntimeError("COSO_API_KEY obbligatoria")
    fps = float(os.getenv("CAMERA_FPS", "5"))
    age = float(os.getenv("CAMERA_MAX_AGE_SECONDS", "3"))
    size = int(os.getenv("CAMERA_MAX_FRAME_BYTES", "524288"))
    viewers = int(os.getenv("CAMERA_MAX_VIEWERS", "2"))
    if not 0 < fps <= 30 or not math.isfinite(age) or age <= 0 or size <= 0 or not 1 <= viewers <= 8:
        raise ValueError("Configurazione camera non valida")
    url = os.getenv("CAMERA_STREAM_URL", "http://192.168.1.137:81/stream")
    if not url.startswith(("http://", "https://")):
        raise ValueError("CAMERA_STREAM_URL deve usare HTTP/HTTPS")
    if hasattr(os, "nice"):
        os.nice(10)
    logging.basicConfig(level=logging.INFO)
    buffer = FrameBuffer(max_age=age, max_bytes=size)
    reader = CameraReader(url, buffer, fps=fps)
    server = RelayServer(
        ("0.0.0.0", int(os.getenv("CAMERA_PORT", "8001"))), buffer, key, viewers, fps,
        username=os.getenv("CAMERA_USERNAME", ""), password=os.getenv("CAMERA_PASSWORD", ""),
    )
    thread = threading.Thread(target=reader.run, daemon=True)
    thread.start()
    try:
        server.serve_forever()
    finally:
        reader.stop.set()
        server.server_close()
        thread.join(timeout=4)


if __name__ == "__main__":
    main()
