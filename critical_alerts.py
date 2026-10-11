"""Poll Vazifa for critical alerts (key-scoped); announce on the desk phone; ack."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

import truststore

truststore.inject_into_ssl()

import config

AnnounceFn = Callable[[list[dict[str, Any]]], bool]


def _key_preview(key: str) -> str:
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}…{key[-3:]}"


def _post_json(url: str, body: dict[str, Any], timeout: float = 20.0) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Critical-Key": body.get("key") or "",
            "Authorization": f"Bearer {body.get('key') or ''}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"ok": False, "raw": raw[:300]}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "data": parsed}
    return status, parsed


def poll_critical(base_url: str, key: str) -> tuple[int, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/critical/poll"
    return _post_json(url, {"key": key})


def ack_critical(base_url: str, key: str, ids: list[str]) -> tuple[int, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/critical/ack"
    return _post_json(url, {"key": key, "ids": ids})


_CRITICAL_TTS_STYLE = (
    "Serious clear adult phone voice. Calm urgency, not panicked. "
    "Speak the alert text exactly. Neutral, intelligible, slightly slow."
)

# ISO timestamps in parentheses / bare — bad for TTS
_TS_RE = re.compile(
    r"\s*[\(\[]?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?[\)\]]?\s*",
    re.IGNORECASE,
)
_SHORT_SPEAK_RE = re.compile(
    r"^(urgent|critical|warning|info|test)([.\s].*)?$",
    re.IGNORECASE,
)


def _clean_spoken_text(text: str) -> str:
    text = _TS_RE.sub(" ", (text or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .,;")
    return text


def _prepare_message_for_tts(text: str) -> str:
    """Shape Uzbek alert text so Edge reads it more clearly on the phone."""
    text = _clean_spoken_text(text)
    if not text:
        return text
    # Drop English severity labels sometimes glued onto speak/message
    text = re.sub(
        r"^(urgent|critical|warning|info|test)\s*[.:\-–—]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    # Pause at commas — Edge often rushes classical/long Uzbek lines
    text = re.sub(r"\s*,\s*", ". ", text)
    # Normalize common Latin Uzbek letters Edge mishandles
    text = (
        text.replace("ʻ", "'")
        .replace("ʼ", "'")
        .replace("oʻ", "o'")
        .replace("gʻ", "g'")
        .replace("Oʻ", "O'")
        .replace("Gʻ", "G'")
    )
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _alert_speak_line(alert: dict[str, Any]) -> str:
    """Prefer full message text for the phone; short speak labels are fallback only."""
    message = _prepare_message_for_tts(str(alert.get("message") or ""))
    speak = _prepare_message_for_tts(str(alert.get("speak") or ""))
    title = _prepare_message_for_tts(str(alert.get("title") or ""))

    # Full message wins when present (Vazifa speak is often a short label).
    if message and (not speak or len(message) > len(speak) + 8 or _SHORT_SPEAK_RE.match(speak)):
        return message
    if speak:
        return speak
    if title:
        return title
    return "Critical alert."

def _alert_id(alert: dict[str, Any]) -> str:
    return str(alert.get("id") or "").strip()


def combine_alert_speaks(alerts: list[dict[str, Any]]) -> str:
    """Summarize one or many alerts into a single spoken body."""
    lines = [_alert_speak_line(a) for a in alerts]
    lines = [x for x in lines if x]
    if not lines:
        return "Critical alert."
    if len(lines) == 1:
        return lines[0]
    parts = [f"Jami {len(lines)} ta xabar."]
    for i, line in enumerate(lines, start=1):
        parts.append(f"{i}. {line}")
    return " ".join(parts)


def build_announcement_script(alerts: list[dict[str, Any]]) -> str:
    """Human-readable script for logs (message + bridge + message)."""
    body = combine_alert_speaks(alerts).strip() or "Critical alert."
    bridge = (getattr(config, "CRITICAL_REPEAT_BRIDGE", "qaytaraman") or "qaytaraman").strip()
    repeats = max(1, int(getattr(config, "CRITICAL_REPEAT_COUNT", 2) or 2))
    if repeats <= 1:
        return body
    return f"{body}. {bridge}. {body}."


def _tts_alert_wav(speak: str) -> tuple[bytes, str]:
    """Edge TTS for the message (critical rate/pitch); Gemini if Edge fails."""
    import asyncio

    from generate_greeting import (
        _edge_speak_latin,
        _GEMINI_TTS_MODELS,
        _is_rate_limited,
        synthesize_edge,
        synthesize_gemini,
    )

    voice = getattr(config, "EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")
    rate = getattr(config, "CRITICAL_TTS_RATE", None) or getattr(config, "EDGE_TTS_RATE", "-20%")
    pitch = getattr(config, "CRITICAL_TTS_PITCH", None) or getattr(config, "EDGE_TTS_PITCH", "-2Hz")
    edge_speak = _edge_speak_latin(_prepare_message_for_tts(speak))
    try:
        print(f"     try edge-tts speak={edge_speak!r} rate={rate} ...", flush=True)
        return asyncio.run(synthesize_edge(edge_speak, voice, rate, pitch)), "edge"
    except Exception as exc:  # noqa: BLE001
        print(f"     edge failed: {exc}", flush=True)
        last_err: BaseException = exc

    for model in _GEMINI_TTS_MODELS:
        try:
            print(f"     try gemini:{model} ...", flush=True)
            return synthesize_gemini(edge_speak, _CRITICAL_TTS_STYLE, model=model), f"gemini:{model}"
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if _is_rate_limited(exc):
                print(f"     rate-limited on {model}, next...", flush=True)
                continue
            print(f"     {model} failed: {exc}", flush=True)
    raise RuntimeError(f"critical TTS failed; last error: {last_err}")


def _bridge_wav_path():
    from pathlib import Path

    path = Path(getattr(config, "CRITICAL_BRIDGE_WAV", "") or "")
    if path.is_file():
        return path
    # Frozen/exe fallback: assets/critical/qaytaraman.wav under resource root
    from app_paths import resource_dir

    fallback = resource_dir() / "assets" / "critical" / "qaytaraman.wav"
    return fallback if fallback.is_file() else None


def _play_lead_silence(bridge, ms: int) -> None:
    """Push short PCM silence so the first spoken syllables aren't clipped after answer."""
    if ms <= 0:
        return
    # Gemini TX path is 24 kHz mono s16le
    n = int(24000 * (ms / 1000.0))
    pcm = b"\x00\x00" * n
    chunk = 1920
    for i in range(0, len(pcm), chunk):
        bridge.push_gemini_pcm24k(pcm[i : i + chunk])
    bridge.wait_tx_idle(timeout=5.0)


def _play_wav_path(bridge, path, *, label: str) -> None:
    from greeting import play_wav

    print(f"[critical] play {label}: {path}", flush=True)
    play_wav(bridge, path, label=f"critical-{label}")
    bridge.wait_tx_idle(timeout=180.0)
    time.sleep(0.3)


def announce_alerts_on_phone(
    pbx,
    bind_ip: str,
    extension: str,
    alerts: list[dict[str, Any]],
) -> bool:
    """Dial once: TTS message once, play it, prerecorded qaytaraman, same message again."""
    if not alerts:
        return True
    if pbx.call:
        print("[critical] phone busy — will retry", flush=True)
        return False

    import tempfile
    from pathlib import Path

    from rtp_bridge import RtpBridge

    body = combine_alert_speaks(alerts).strip() or "Critical alert."
    repeats = max(1, int(getattr(config, "CRITICAL_REPEAT_COUNT", 2) or 2))
    ring_sec = float(getattr(config, "CRITICAL_RING_SEC", 15) or 15)
    print(
        f"[critical] TTS once for {extension}: {body!r} "
        f"(then bridge wav, then replay x{repeats})",
        flush=True,
    )

    ok = pbx.dial(extension, ring_seconds=ring_sec, connect_tone=False)
    if not ok or not pbx.call or not pbx.call.remote_rtp:
        print(f"[critical] no answer / dial failed (rang {ring_sec:.0f}s)", flush=True)
        return False

    call = pbx.call
    rtp_bind = bind_ip
    if bind_ip in ("0.0.0.0", "::", "[::]"):
        rtp_bind = pbx.advertise_ip

    bridge = RtpBridge(
        bind_ip=rtp_bind,
        local_port=call.local_rtp,
        remote=call.remote_rtp,
    )
    msg_path: Path | None = None
    try:
        bridge.start()
        # Let RTP settle — otherwise the opening words of the message get clipped.
        lead_ms = int(getattr(config, "CRITICAL_LEAD_SILENCE_MS", 600) or 0)
        _play_lead_silence(bridge, lead_ms)

        wav, engine = _tts_alert_wav(body)
        print(f"[critical] TTS via {engine}", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav)
            msg_path = Path(tmp.name)

        # 1) message  2) prerecorded qaytaraman  3) same message file again
        _play_wav_path(bridge, msg_path, label="msg-1")
        if repeats >= 2:
            if not pbx.call:
                print("[critical] call dropped mid-announce", flush=True)
                return False
            bridge_wav = _bridge_wav_path()
            if bridge_wav is None:
                # Last resort: one-shot TTS for bridge word only
                print("[critical] bridge wav missing — synthesizing once", flush=True)
                btext = (getattr(config, "CRITICAL_REPEAT_BRIDGE", "qaytaraman") or "qaytaraman")
                bwav, _ = _tts_alert_wav(btext)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(bwav)
                    bridge_wav = Path(tmp.name)
                try:
                    _play_wav_path(bridge, bridge_wav, label="bridge")
                finally:
                    try:
                        bridge_wav.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                _play_wav_path(bridge, bridge_wav, label="bridge")
            if not pbx.call:
                print("[critical] call dropped mid-announce", flush=True)
                return False
            _play_wav_path(bridge, msg_path, label="msg-2")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[critical] announce error: {exc}", flush=True)
        return False
    finally:
        if msg_path is not None:
            try:
                msg_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            bridge.stop()
        except Exception:
            pass
        try:
            if pbx.call:
                pbx.hangup()
        except Exception:
            pass


def _merge_alerts(
    pending: dict[str, dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> int:
    """Merge by id; return how many new ids were added."""
    added = 0
    for alert in incoming:
        if not isinstance(alert, dict):
            continue
        aid = _alert_id(alert)
        if not aid:
            # no id — keep a synthetic key so it still gets spoken once
            aid = f"_noid_{len(pending)}_{hash(json.dumps(alert, sort_keys=True, default=str)) & 0xFFFF}"
            alert = {**alert, "id": aid}
        if aid not in pending:
            pending[aid] = alert
            added += 1
        else:
            # refresh wording if server updated the same id
            pending[aid] = alert
    return added


class CriticalAlertPoller:
    def __init__(
        self,
        *,
        base_url: str,
        key: str,
        announce: AnnounceFn,
        default_interval: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.announce = announce
        self.default_interval = max(5.0, float(default_interval))
        self.ring_sec = float(getattr(config, "CRITICAL_RING_SEC", 15) or 15)
        self.retry_gap_sec = float(getattr(config, "CRITICAL_RETRY_GAP_SEC", 60) or 60)
        self.max_attempts = max(1, int(getattr(config, "CRITICAL_MAX_ATTEMPTS", 5) or 5))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._attempt = 0
        self._next_attempt_at = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="vazifa-critical",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[critical] polling {self.base_url} key={_key_preview(self.key)} "
            f"every ~{self.default_interval:.0f}s | ring={self.ring_sec:.0f}s "
            f"retry={self.retry_gap_sec:.0f}s x{self.max_attempts} "
            f"tts-once + bridge wav + replay "
            f"({getattr(config, 'CRITICAL_REPEAT_BRIDGE', 'qaytaraman')!r})",
            flush=True,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3.0)

    def _poll_once(self) -> tuple[float, list[dict[str, Any]], str]:
        """Return (interval, alerts, username). Empty alerts on soft failures."""
        interval = self.default_interval
        try:
            status, body = poll_critical(self.base_url, self.key)
        except Exception as exc:  # noqa: BLE001
            print(f"[critical] poll error: {exc}", flush=True)
            return 10.0, [], "?"

        if status == 401:
            print("[critical] 401 auth — check VAZIFA_CRITICAL_KEY", flush=True)
            return 30.0, [], "?"
        if status != 200:
            print(f"[critical] poll HTTP {status}: {body}", flush=True)
            return 10.0, [], "?"

        try:
            interval = float(body.get("pollIntervalHintSec") or interval)
        except (TypeError, ValueError):
            pass
        interval = max(5.0, interval)

        alerts = body.get("alerts") or []
        if not isinstance(alerts, list):
            alerts = []
        has = bool(body.get("hasCritical"))
        count = int(body.get("count") or len(alerts))
        if not has or count == 0 or not alerts:
            return interval, [], str(body.get("username") or "?")
        return interval, alerts, str(body.get("username") or "?")

    def _ack_pending(self) -> None:
        ids = [aid for aid in self._pending if not aid.startswith("_noid_")]
        if not ids:
            self._pending.clear()
            return
        try:
            st, ack_body = ack_critical(self.base_url, self.key, ids)
            if st == 200:
                print(f"[critical] acked {len(ids)} id(s)", flush=True)
            else:
                print(f"[critical] ack HTTP {st}: {ack_body}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[critical] ack error: {exc}", flush=True)
        self._pending.clear()

    def _loop(self) -> None:
        interval = self.default_interval
        while not self._stop.is_set():
            interval, incoming, who = self._poll_once()
            if incoming:
                added = _merge_alerts(self._pending, incoming)
                if added:
                    print(
                        f"[critical] +{added} new alert(s) for {who} "
                        f"(pending={len(self._pending)}) — will summarize on next speak",
                        flush=True,
                    )
                    # New message during a retry wave: speak ASAP (don't wait full gap)
                    if self._attempt > 0:
                        self._next_attempt_at = min(self._next_attempt_at, time.time())

            if not self._pending:
                self._attempt = 0
                self._next_attempt_at = 0.0
                self._stop.wait(interval)
                continue

            now = time.time()
            if now < self._next_attempt_at:
                wait = min(interval, self._next_attempt_at - now)
                self._stop.wait(max(0.5, wait))
                continue

            if self._attempt >= self.max_attempts:
                print(
                    f"[critical] gave up after {self.max_attempts} attempts "
                    f"({len(self._pending)} still pending) — will try again later",
                    flush=True,
                )
                self._attempt = 0
                self._next_attempt_at = now + self.retry_gap_sec
                self._stop.wait(min(interval, self.retry_gap_sec))
                continue

            self._attempt += 1
            print(
                f"[critical] call attempt {self._attempt}/{self.max_attempts} "
                f"({len(self._pending)} alert(s), ring {self.ring_sec:.0f}s)",
                flush=True,
            )
            spoken_ok = False
            try:
                spoken_ok = bool(self.announce(list(self._pending.values())))
            except Exception as exc:  # noqa: BLE001
                print(f"[critical] announce failed: {exc}", flush=True)

            if spoken_ok:
                self._ack_pending()
                self._attempt = 0
                self._next_attempt_at = 0.0
                self._stop.wait(interval)
                continue

            self._next_attempt_at = time.time() + self.retry_gap_sec
            print(
                f"[critical] retry in {self.retry_gap_sec:.0f}s "
                f"(attempt {self._attempt}/{self.max_attempts})",
                flush=True,
            )
            self._stop.wait(min(interval, self.retry_gap_sec))
