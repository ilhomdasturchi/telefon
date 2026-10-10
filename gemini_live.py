"""
Gemini Live API voice session.

Streams 16 kHz PCM16 audio in and receives 24 kHz PCM16 audio out over a
persistent WebSocket session (google-genai SDK).
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Callable, Optional

# Corporate antivirus / proxy MITM: use Windows system CA store.
import truststore

truststore.inject_into_ssl()

from google import genai
from google.genai import types

import config

PCM_IN_RATE = 16000
PCM_OUT_RATE = 24000


def _normalize_uz(text: str) -> str:
    return (
        text.lower()
        .replace("‘", "'")
        .replace("’", "'")
        .replace("ʻ", "'")
        .replace("ʼ", "'")
        .replace("`", "'")
        .strip()
    )


def text_requests_hangup(text: str) -> bool:
    """True if AI farewell text means the call should end."""
    if not text:
        return False
    norm = _normalize_uz(text)
    phrases = getattr(config, "HANGUP_PHRASES", ())
    return any(_normalize_uz(p) in norm for p in phrases)


class GeminiLiveSession:
    """Thread-friendly wrapper around an async Gemini Live session."""

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        on_audio_out: Optional[Callable[[bytes], None]] = None,
        on_interrupted: Optional[Callable[[], None]] = None,
        on_hang_up: Optional[Callable[[], None]] = None,
        on_user_speech: Optional[Callable[[], None]] = None,
        on_model_speech: Optional[Callable[[], None]] = None,
        greet_on_start: bool = True,
    ):
        self.api_key = api_key
        self.model = model or config.GEMINI_LIVE_MODEL
        self.system_instruction = system_instruction or config.GEMINI_SYSTEM_INSTRUCTION
        self.on_audio_out = on_audio_out
        self.on_interrupted = on_interrupted
        self.on_hang_up = on_hang_up
        self.on_user_speech = on_user_speech
        self.on_model_speech = on_model_speech
        self.greet_on_start = greet_on_start

        self._in_q: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=200)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._stop = threading.Event()
        self._started = threading.Event()
        self._error: Optional[BaseException] = None
        self._audio_out_bytes = 0
        self._audio_in_chunks = 0
        self._send_buf = bytearray()
        self._send_lock = threading.Lock()
        # ~100ms @ 16kHz mono 16-bit
        self._send_chunk_bytes = int(PCM_IN_RATE * 0.1) * 2
        self._out_text_turn = ""
        self._hangup_requested = False
        self._hangup_audio_until = 0.0
        # When True, hang_up tool / farewell-text detection are ignored (idle farewell playback).
        self.ignore_hangup_signals = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self, timeout: float = 30.0) -> None:
        if self.running:
            return
        self._stop.clear()
        self._error = None
        self._started.clear()
        self._audio_out_bytes = 0
        self._audio_in_chunks = 0
        self._out_text_turn = ""
        self._hangup_requested = False
        self._hangup_audio_until = 0.0
        self.ignore_hangup_signals = False
        with self._send_lock:
            self._send_buf.clear()
        self._thread = threading.Thread(target=self._thread_main, name="gemini-live", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            self.close()
            raise TimeoutError("Gemini Live session failed to start in time")
        if self._error:
            err = self._error
            self.close()
            raise RuntimeError(f"Gemini Live failed: {err}") from err

    def send_pcm16_16k(self, chunk: bytes) -> None:
        if not chunk or self._stop.is_set() or self._hangup_requested:
            return
        # Buffer into ~100ms frames — better for Live ASR than tiny RTP slices
        with self._send_lock:
            self._send_buf.extend(chunk)
            while len(self._send_buf) >= self._send_chunk_bytes:
                frame = bytes(self._send_buf[: self._send_chunk_bytes])
                del self._send_buf[: self._send_chunk_bytes]
                self._enqueue_audio(frame)

    def send_text(self, text: str) -> bool:
        """Inject a text turn into the live session (idle prompts, etc.)."""
        if not text or self._stop.is_set() or self._hangup_requested:
            return False
        if not self._loop or not self._session:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._session.send_realtime_input(text=text),
                self._loop,
            )
            fut.result(timeout=5)
            print(f"[gemini] injected text: {text[:120]}", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[gemini] send_text error: {exc}", flush=True)
            return False

    def _enqueue_audio(self, chunk: bytes) -> None:
        self._audio_in_chunks += 1
        if self._audio_in_chunks in {1, 25, 100}:
            print(f"[gemini] uplink pcm frames={self._audio_in_chunks}", flush=True)
        try:
            self._in_q.put_nowait(chunk)
        except queue.Full:
            try:
                self._in_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._in_q.put_nowait(chunk)
            except queue.Full:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._in_q.put_nowait(None)
        except queue.Full:
            pass
        if self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_session(), self._loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._session = None

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as exc:  # noqa: BLE001
            self._error = exc
            print(f"[gemini] session error: {exc}", flush=True)
        finally:
            self._started.set()
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _shutdown_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass

    async def _run(self) -> None:
        client = genai.Client(api_key=self.api_key)
        uz_asr = types.AudioTranscriptionConfig(
            language_codes=["uz-UZ", "uz"],
            custom_vocabulary=[
                "labbay",
                "eshitaman",
                "allo",
                "alyo",
                "yaxshimisiz",
                "assalomu aleykum",
                "nima xizmat",
                "rahmat",
                "ha",
                "yo'q",
            ],
        )
        # Free local tools (CBU rates, weather) — no Google Search billing.
        # Google Search is paid-tier only and causes Live 1011 on free keys.
        from free_facts import run_tool as run_free_tool

        self._run_free_tool = run_free_tool
        fact_tools = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_exchange_rate",
                    description=(
                        "Get official Uzbekistan Central Bank (CBU) exchange rate "
                        "to UZS. Use for dollar, euro, ruble, etc."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "currency": {
                                "type": "string",
                                "description": "ISO code, e.g. USD, EUR, RUB",
                            }
                        },
                    },
                ),
                types.FunctionDeclaration(
                    name="get_weather",
                    description="Get current weather for a Uzbekistan city (free).",
                    parameters={
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "City name, e.g. Toshkent, Samarqand",
                            }
                        },
                    },
                ),
                types.FunctionDeclaration(
                    name="hang_up",
                    description=(
                        "End the phone call after you have said a short farewell. "
                        "Call this only when the user no longer needs help."
                    ),
                ),
            ]
        )
        tools: list = [fact_tools]
        if getattr(config, "GEMINI_LIVE_GOOGLE_SEARCH", False):
            tools.insert(0, types.Tool(google_search=types.GoogleSearch()))
            print("[gemini] Google Search grounding ON", flush=True)
        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=self.system_instruction,
            tools=tools,
            speech_config=types.SpeechConfig(language_code="uz-UZ"),
            input_audio_transcription=uz_asr,
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=["uz-UZ", "uz"]
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=200,
                    silence_duration_ms=900,
                )
            ),
        )
        print(f"[gemini] connecting model={self.model}", flush=True)
        async with client.aio.live.connect(model=self.model, config=live_config) as session:
            self._session = session
            self._started.set()
            print("[gemini] live session started (uz-UZ ASR hints)", flush=True)

            if self.greet_on_start:
                await session.send_realtime_input(
                    text="Foydalanuvchi telefonni oldi. Bir qisqa o'zbekcha salom ayting."
                )

            sender = asyncio.create_task(self._send_loop(session))
            receiver = asyncio.create_task(self._recv_loop(session))
            stopper = asyncio.create_task(self._wait_stop())
            done, pending = await asyncio.wait(
                {sender, receiver, stopper},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc

    async def _wait_stop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.05)

    async def _send_loop(self, session) -> None:
        while not self._stop.is_set():
            chunk = await asyncio.to_thread(self._in_q.get)
            if chunk is None:
                break
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={PCM_IN_RATE}")
            )

    async def _recv_loop(self, session) -> None:
        # session.receive() ends after each model turn — keep requesting turns.
        while not self._stop.is_set():
            try:
                async for response in session.receive():
                    if self._stop.is_set():
                        return
                    await self._handle_response(session, response)
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    return
                print(f"[gemini] receive error: {exc}", flush=True)
                await asyncio.sleep(0.2)

    def _request_hang_up(self, reason: str) -> None:
        if self._hangup_requested:
            return
        if self.ignore_hangup_signals:
            print(f"[gemini] hang up deferred ({reason})", flush=True)
            return
        self._hangup_requested = True
        # Short grace so the current farewell phrase can finish; then drop audio.
        self._hangup_audio_until = time.time() + 1.2
        with self._send_lock:
            self._send_buf.clear()
        try:
            self._in_q.put_nowait(None)
        except queue.Full:
            pass
        print(f"[gemini] hang up requested ({reason})", flush=True)
        if not self.on_hang_up:
            return
        threading.Thread(
            target=self._safe_hang_up,
            name="ai-hangup",
            daemon=True,
        ).start()

    def _safe_hang_up(self) -> None:
        try:
            self.on_hang_up()
        except Exception as exc:  # noqa: BLE001
            print(f"[gemini] on_hang_up error: {exc}", flush=True)

    def _accepting_audio(self) -> bool:
        if not self._hangup_requested:
            return True
        return time.time() <= self._hangup_audio_until

    async def _handle_tool_call(self, session, tool_call) -> None:
        responses = []
        for fc in getattr(tool_call, "function_calls", None) or []:
            name = getattr(fc, "name", "") or ""
            args = dict(getattr(fc, "args", None) or {})
            print(f"[gemini] tool call: {name} args={args}", flush=True)
            if name == "hang_up":
                self._request_hang_up("tool hang_up")
                payload = {
                    "ok": True,
                    "status": "hanging_up",
                    "instruction": "Stay silent. Do not speak again.",
                }
            elif name in {"get_exchange_rate", "get_weather"}:
                runner = getattr(self, "_run_free_tool", None)
                if runner is None:
                    from free_facts import run_tool as runner

                    self._run_free_tool = runner
                payload = await asyncio.to_thread(runner, name, args)
                print(f"[gemini] tool result: {payload}", flush=True)
            else:
                payload = {"ok": False, "error": f"unknown tool {name}"}
            responses.append(
                types.FunctionResponse(
                    id=getattr(fc, "id", None),
                    name=name,
                    response=payload,
                )
            )
        if responses:
            try:
                await session.send_tool_response(function_responses=responses)
            except Exception as exc:  # noqa: BLE001
                print(f"[gemini] tool response error: {exc}", flush=True)

    def _emit_user_speech(self) -> None:
        if not self.on_user_speech:
            return
        try:
            self.on_user_speech()
        except Exception:
            pass

    async def _handle_response(self, session, response) -> None:
        tool_call = getattr(response, "tool_call", None)
        if tool_call:
            await self._handle_tool_call(session, tool_call)

        # Live VAD: user started talking (more reliable than mic RMS for idle).
        va = getattr(response, "voice_activity", None)
        if va is not None and not self._hangup_requested:
            kind = getattr(va, "voice_activity_type", None)
            name = getattr(kind, "name", str(kind) if kind is not None else "")
            if "ACTIVITY_START" in name:
                print("[gemini] voice activity START", flush=True)
                self._emit_user_speech()

        sc = getattr(response, "server_content", None)
        if not sc:
            return

        if getattr(sc, "interrupted", False) and not self._hangup_requested:
            print("[gemini] interrupted (barge-in)", flush=True)
            self._out_text_turn = ""
            self._emit_user_speech()
            if self.on_interrupted:
                try:
                    self.on_interrupted()
                except Exception:
                    pass

        in_tr = getattr(sc, "input_transcription", None)
        if in_tr and getattr(in_tr, "text", None):
            print(f"[gemini] you: {in_tr.text}", flush=True)
            if str(in_tr.text).strip():
                self._emit_user_speech()
        out_tr = getattr(sc, "output_transcription", None)
        if out_tr and getattr(out_tr, "text", None):
            chunk = out_tr.text
            print(f"[gemini] ai: {chunk}", flush=True)
            if not self._hangup_requested:
                if self.on_model_speech:
                    try:
                        self.on_model_speech()
                    except Exception:
                        pass
                if chunk.startswith(self._out_text_turn):
                    self._out_text_turn = chunk
                elif self._out_text_turn.endswith(chunk):
                    pass
                else:
                    self._out_text_turn += chunk

        if getattr(sc, "turn_complete", False) and not self._hangup_requested:
            if text_requests_hangup(self._out_text_turn):
                self._request_hang_up(f"farewell: {self._out_text_turn!r}")
            self._out_text_turn = ""

        if not self._accepting_audio():
            return

        turn = getattr(sc, "model_turn", None)
        if not turn:
            return
        for part in turn.parts or []:
            inline = getattr(part, "inline_data", None)
            if not inline or not inline.data:
                continue
            data = inline.data
            if isinstance(data, str):
                import base64

                data = base64.b64decode(data)
            self._audio_out_bytes += len(data)
            if self._audio_out_bytes <= len(data) or self._audio_out_bytes % 48000 < len(data):
                print(
                    f"[gemini] downlink audio +{len(data)}B total={self._audio_out_bytes}B",
                    flush=True,
                )
            if self.on_model_speech:
                try:
                    self.on_model_speech()
                except Exception:
                    pass
            if self.on_audio_out:
                try:
                    self.on_audio_out(data)
                except Exception as exc:  # noqa: BLE001
                    print(f"[gemini] on_audio_out error: {exc}", flush=True)
