"""Local HTTP API: trigger outbound desk-phone call (announce or short ring)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import config

# Shared with critical poller — only one outbound call at a time
_call_lock = threading.Lock()


def call_lock() -> threading.Lock:
    return _call_lock


AnnounceFn = Callable[..., bool]


def ring_only(
    pbx,
    *,
    extension: str,
    ring_seconds: float,
) -> dict[str, Any]:
    """INVITE then hang up after ring_seconds (no answer / no TTS required)."""
    if pbx.call:
        return {"ok": False, "error": "busy", "message": "Phone already in a call"}
    reg = pbx.registrations.get(extension)
    if not reg or not getattr(reg, "alive", True):
        return {
            "ok": False,
            "error": "not_registered",
            "message": f"Extension {extension} is not registered",
        }

    ring_seconds = max(0.5, min(10.0, float(ring_seconds)))
    print(f"[call-api] ring-only ext={extension} for {ring_seconds:.1f}s", flush=True)
    # dial() returns False on no-answer; for ring-only that is expected success
    pbx.dial(extension, ring_seconds=ring_seconds, connect_tone=False)
    if pbx.call:
        try:
            pbx.hangup()
        except Exception:
            pass
    return {
        "ok": True,
        "type": "ring",
        "extension": extension,
        "ringSeconds": ring_seconds,
    }


def announce_message(
    announce: AnnounceFn,
    *,
    text: str,
    extension: str,
) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "missing_message", "message": "message/speak required"}
    print(f"[call-api] announce ext={extension} text={text!r}", flush=True)
    alerts = [{"id": f"api-{int(time.time())}", "speak": text, "message": text}]
    try:
        ok = bool(announce(alerts))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "announce_failed", "message": str(exc)}
    if not ok:
        return {
            "ok": False,
            "error": "announce_unsuccessful",
            "message": "No answer, busy, or TTS/play failed",
        }
    return {"ok": True, "type": "announce", "extension": extension, "message": text}


class CallApiServer:
    def __init__(
        self,
        *,
        pbx,
        bind_ip: str,
        extension: str,
        announce: AnnounceFn,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
        default_ring_sec: float | None = None,
    ):
        self.pbx = pbx
        self.bind_ip = bind_ip
        self.extension = extension
        self.announce = announce
        self.host = (host if host is not None else getattr(config, "CALL_API_HOST", "0.0.0.0")).strip() or "0.0.0.0"
        self.port = int(port if port is not None else getattr(config, "CALL_API_PORT", 8088))
        self.api_key = (
            api_key
            if api_key is not None
            else (getattr(config, "CALL_API_KEY", "") or "")
        ).strip()
        self.default_ring_sec = float(
            default_ring_sec
            if default_ring_sec is not None
            else getattr(config, "CALL_API_RING_SEC", 2.0)
        )
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.api_key:
            print(
                "[call-api] disabled — set CALL_API_KEY in .env to enable POST /api/call",
                flush=True,
            )
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # quieter
                print(f"[call-api] {self.address_string()} {fmt % args}", flush=True)

            def _json(self, code: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth_ok(self, body: dict[str, Any]) -> bool:
                if not outer.api_key:
                    return False
                hdr = (
                    self.headers.get("X-Call-Key")
                    or self.headers.get("X-Api-Key")
                    or ""
                ).strip()
                auth = (self.headers.get("Authorization") or "").strip()
                if auth.lower().startswith("bearer "):
                    hdr = hdr or auth[7:].strip()
                key = hdr or str(body.get("key") or "").strip()
                return key == outer.api_key

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path in ("/api/health", "/health"):
                    reg = outer.extension in outer.pbx.registrations
                    self._json(
                        200,
                        {
                            "ok": True,
                            "service": "telefon-call-api",
                            "extension": outer.extension,
                            "registered": reg,
                            "busy": bool(outer.pbx.call),
                            "types": ["announce", "ring"],
                        },
                    )
                    return
                self._json(404, {"ok": False, "error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path not in ("/api/call", "/call"):
                    self._json(404, {"ok": False, "error": "not_found"})
                    return

                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(max(0, length)) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(400, {"ok": False, "error": "invalid_json"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"ok": False, "error": "invalid_json"})
                    return

                if not self._auth_ok(body):
                    self._json(401, {"ok": False, "error": "unauthorized"})
                    return

                call_type = str(body.get("type") or body.get("mode") or "").strip().lower()
                if call_type in ("message", "speak", "play", "tts", "announce"):
                    call_type = "announce"
                elif call_type in ("ring", "ping", "short", "buzz"):
                    call_type = "ring"
                else:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "bad_type",
                            "message": 'type must be "announce" or "ring"',
                        },
                    )
                    return

                ext = str(body.get("extension") or body.get("ext") or outer.extension).strip()
                text = str(
                    body.get("message")
                    or body.get("speak")
                    or body.get("text")
                    or ""
                ).strip()

                if not _call_lock.acquire(blocking=False):
                    self._json(409, {"ok": False, "error": "busy", "message": "Another call in progress"})
                    return
                try:
                    if call_type == "ring":
                        ring_sec = body.get("ringSeconds", body.get("ring_seconds"))
                        if ring_sec is None:
                            ring_sec = outer.default_ring_sec
                        result = ring_only(
                            outer.pbx,
                            extension=ext,
                            ring_seconds=float(ring_sec),
                        )
                        self._json(200 if result.get("ok") else 409, result)
                        return

                    # announce — same path as Vazifa critical (TTS once + qaytaraman + replay)
                    result = announce_message(outer.announce, text=text, extension=ext)
                    self._json(200 if result.get("ok") else 409, result)
                finally:
                    _call_lock.release()

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            print(f"[call-api] could not bind {self.host}:{self.port}: {exc}", flush=True)
            return

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="call-api",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[call-api] listening http://{self.host}:{self.port}/api/call "
            f"(types: announce | ring)",
            flush=True,
        )

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
