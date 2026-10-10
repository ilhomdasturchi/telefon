"""
AI desk phone: pick up / dial the PBX → auto-answer → talk with Gemini Live.

Usage:
  1. Copy .env.example to .env and set GEMINI_API_KEY
  2. pip install -r requirements.txt
  3. python ai_phone.py

Extensions:
  100 = AI (Gemini)
  101 = desk phone

Phone tip (true off-hook connect):
  On KX-HDV130 set Hot Line to dial 100 toward registrar 192.168.1.100.
  Or go off-hook and dial 100.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time

from dotenv import load_dotenv

from app_paths import env_path

# Load .env before config so BIND_IP / GEMINI_* apply at import time.
load_dotenv(env_path())

import truststore

truststore.inject_into_ssl()

import config
from autostart import ensure_autostart, remove_autostart
from gemini_live import GeminiLiveSession
from greeting import pick_random_phrase, play_random_greeting, play_wav
from rtp_bridge import RtpBridge
from sip_pbx import CallSession, SipPbx

# Idle states for silence watchdog
_IDLE_ACTIVE = "active"
_IDLE_OFFERED = "offered"
_IDLE_ENDING = "ending"


class AiCallController:
    def __init__(self, api_key: str, model: str, bind_ip: str, pbx: SipPbx | None = None):
        self.api_key = api_key
        self.model = model
        self.bind_ip = bind_ip
        self.pbx = pbx
        self._lock = threading.Lock()
        self._gemini: GeminiLiveSession | None = None
        self._bridge: RtpBridge | None = None
        self._hanging_up = False
        self._idle_stop = threading.Event()
        self._idle_thread: threading.Thread | None = None
        self._idle_state = _IDLE_ACTIVE
        self._silence_elapsed = 0.0
        self._idle_last_tick = 0.0
        self._farewell = ""
        self._idle_farewell = ""
        self._help_offer = ""
        self._farewell_wav = None
        self._idle_farewell_wav = None
        self._help_offer_wav = None
        self._scripted_until = 0.0

    def attach_pbx(self, pbx: SipPbx) -> None:
        self.pbx = pbx

    def _pick_call_phrases(self) -> None:
        # Farewell for when the model ends the call (system instruction).
        # Help-offer / idle-farewell are re-picked each silence event.
        self._farewell, self._farewell_wav = pick_random_phrase(
            getattr(config, "FAREWELL_PHRASES", ())
        )
        self._idle_farewell = ""
        self._idle_farewell_wav = None
        self._help_offer = ""
        self._help_offer_wav = None

    def _next_help_offer(self) -> tuple[str, object]:
        text, wav = pick_random_phrase(
            getattr(config, "HELP_OFFER_PHRASES", ()),
            exclude_speak=self._help_offer or None,
        )
        self._help_offer, self._help_offer_wav = text, wav
        return text or "Yana biror yordam kerakmi?", wav

    def _next_idle_farewell(self) -> tuple[str, object]:
        text, wav = pick_random_phrase(
            getattr(config, "IDLE_FAREWELL_PHRASES", ())
            or getattr(config, "FAREWELL_PHRASES", ()),
            exclude_speak=self._idle_farewell or None,
        )
        self._idle_farewell, self._idle_farewell_wav = text, wav
        return text or self._farewell or "Xayr!", wav

    def _system_instruction_for_call(self, farewell: str) -> str:
        base = config.GEMINI_SYSTEM_INSTRUCTION.rstrip()
        return (
            f"{base} "
            f"When ending the call (user finished / no longer needs help): "
            f"say exactly this farewell and nothing else: '{farewell}' "
            f"Then immediately call hang_up once. Do not invent another goodbye."
        )

    def _play_phrase_or_speak(self, gemini: GeminiLiveSession, text: str, wav, *, kind: str) -> bool:
        """Play pre-recorded WAV if present; else ask Live to speak. Returns True if WAV used."""
        # Ignore model-speech idle resets while scripted check-in/farewell plays.
        self._scripted_until = time.time() + 12.0
        bridge = self._bridge
        if wav is not None and bridge is not None:
            play_wav(bridge, wav, label=kind)
            print(f"[ai] played prerecorded {kind}: {text!r}", flush=True)
            return True
        gemini.send_text(
            f"Faqat shu gapni ovoz bilan ayt (o'zgartirma): '{text}' "
            "Boshqa hech narsa dema. hang_up chaqirma."
        )
        print(f"[ai] no WAV for {kind}, asked Live to say: {text!r}", flush=True)
        return False

    def note_conversation_activity(self, who: str = "user") -> None:
        """User or AI spoke (real conversation). Resets mutual-silence idle clock."""
        with self._lock:
            if self._hanging_up or self._idle_state == _IDLE_ENDING:
                return
            # Scripted idle lines (WAV or Live) must not look like "conversation resumed".
            if who == "ai" and time.time() < self._scripted_until:
                return
            if self._idle_state == _IDLE_OFFERED and who == "user":
                print("[ai] user spoke after help offer — idle reset", flush=True)
            elif self._idle_state == _IDLE_OFFERED and who == "ai":
                print("[ai] model spoke after help offer — idle reset", flush=True)
            self._idle_state = _IDLE_ACTIVE
            self._silence_elapsed = 0.0
            self._idle_last_tick = time.time()

    def note_user_activity(self) -> None:
        self.note_conversation_activity("user")

    def on_answered(self, call: CallSession) -> None:
        with self._lock:
            self._hanging_up = False
            self._stop_idle_unlocked()
            self._teardown_unlocked()
            if not call.remote_rtp:
                print("[ai] answered but no remote RTP in SDP", flush=True)
                return

            # Bind RTP on the advertised LAN IP (not 0.0.0.0) so:
            # - TX packets leave with the IP the phone expects (symmetric RTP)
            # - RX listens on the same address put in SDP
            rtp_bind = self.bind_ip
            if self.pbx and self.bind_ip in ("0.0.0.0", "::", "[::]"):
                rtp_bind = self.pbx.advertise_ip
            bridge = RtpBridge(
                bind_ip=rtp_bind,
                local_port=call.local_rtp,
                remote=call.remote_rtp,
            )

            gemini = None
            try:
                bridge.start()
                # Instant local greeting (random short phrase, no Gemini wait)
                if play_random_greeting(bridge):
                    bridge.wait_tx_idle(timeout=15.0)
                else:
                    print(
                        "[ai] no greeting WAVs — run: python generate_greeting.py",
                        flush=True,
                    )

                self._pick_call_phrases()
                print(
                    f"[ai] this call farewell={self._farewell!r} "
                    f"(help/idle phrases re-rolled each silence)",
                    flush=True,
                )

                gemini = GeminiLiveSession(
                    api_key=self.api_key,
                    model=self.model,
                    system_instruction=self._system_instruction_for_call(self._farewell),
                    on_audio_out=bridge.push_gemini_pcm24k,
                    on_interrupted=bridge.flush_output,
                    on_hang_up=self.request_hang_up,
                    on_user_speech=self.note_user_activity,
                    on_model_speech=lambda: self.note_conversation_activity("ai"),
                    greet_on_start=False,
                )
                # Mic goes straight to Gemini — idle silence is NOT based on PCM RMS
                # (handset noise kept resetting the timer and blocking help offers).
                bridge.on_pcm16_16k = gemini.send_pcm16_16k
                gemini.start()
            except Exception as exc:  # noqa: BLE001
                print(f"[ai] failed to start media/Gemini: {exc}", flush=True)
                if gemini:
                    gemini.close()
                bridge.stop()
                return

            self._bridge = bridge
            self._gemini = gemini
            self._idle_state = _IDLE_ACTIVE
            self._silence_elapsed = 0.0
            self._idle_last_tick = time.time()
            self._start_idle_unlocked()
            print("[ai] Gemini connected — gapiring (speak)", flush=True)
            if self.pbx:
                print(
                    f"[ai] phone should send RTP to {self.pbx.advertise_ip}:{call.local_rtp} "
                    f"(set SIP_ADVERTISE_IP in .env if this IP is wrong)",
                    flush=True,
                )
            threading.Thread(
                target=self._warn_if_no_rtp,
                args=(bridge,),
                name="rtp-rx-watch",
                daemon=True,
            ).start()

    def _warn_if_no_rtp(self, bridge: RtpBridge) -> None:
        """If the phone never sends mic RTP, Gemini cannot hear the user."""
        time.sleep(4.0)
        with self._lock:
            if self._bridge is not bridge or self._hanging_up:
                return
        audio_rx = getattr(bridge, "_rx_packets", 0)
        any_rx = getattr(bridge, "_rx_datagrams", 0)
        if audio_rx == 0:
            adv = self.pbx.advertise_ip if self.pbx else "?"
            if any_rx:
                print(
                    f"[ai] WARNING: got {any_rx} UDP packet(s) on RTP port but no PCMU/PCMA audio. "
                    f"Check phone codec (use G.711 u-law or A-law).",
                    flush=True,
                )
            else:
                print(
                    f"[ai] WARNING: no UDP at all on {bridge.bind_ip}:{bridge.local_port} — "
                    f"phone mic RTP is not reaching this PC. "
                    f"On the SERVER open Windows Firewall inbound UDP 10000-10200 "
                    f"(and 5060). SIP_ADVERTISE_IP={adv}. RDP is unrelated.",
                    flush=True,
                )

    def _start_idle_unlocked(self) -> None:
        self._idle_stop.clear()
        self._idle_thread = threading.Thread(
            target=self._idle_watchdog,
            name="idle-watchdog",
            daemon=True,
        )
        self._idle_thread.start()

    def _stop_idle_unlocked(self) -> None:
        self._idle_stop.set()
        t = self._idle_thread
        self._idle_thread = None
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)

    def _idle_watchdog(self) -> None:
        offer_after = float(getattr(config, "SILENCE_OFFER_HELP_SECONDS", 10.0))
        hang_after = float(getattr(config, "SILENCE_HANGUP_SECONDS", 10.0))
        print(
            f"[ai] silence watchdog: remind after {offer_after:.0f}s mutual silence, "
            f"farewell+hang after +{hang_after:.0f}s "
            f"(resets when user OR AI speaks)",
            flush=True,
        )
        last_log = 0.0
        while not self._idle_stop.wait(0.25):
            now = time.time()
            with self._lock:
                if self._hanging_up or self._idle_state == _IDLE_ENDING:
                    return
                gemini = self._gemini
                bridge = self._bridge
                state = self._idle_state
                last_tick = self._idle_last_tick or now
                dt = max(0.0, now - last_tick)
                self._idle_last_tick = now

            if gemini is None or not gemini.running:
                continue

            # Phone still playing AI/phrase audio — not mutual silence yet.
            # Reset the clock so post-reply thinking time starts fresh at 0.
            ai_talking = bridge is not None and bridge.is_tx_busy()
            if ai_talking:
                with self._lock:
                    # Keep OFFERED while our check-in WAV plays; only reset elapsed.
                    if self._idle_state == _IDLE_ACTIVE:
                        self._silence_elapsed = 0.0
                    elif self._idle_state == _IDLE_OFFERED:
                        # While help-offer WAV plays, don't advance hang timer.
                        pass
                if now - last_log >= 3.0:
                    print("[ai] silence clock held (audio playing)", flush=True)
                    last_log = now
                if state == _IDLE_OFFERED:
                    continue  # don't count hang time over the offer WAV
                if state == _IDLE_ACTIVE:
                    continue

            with self._lock:
                if self._hanging_up or self._idle_state == _IDLE_ENDING:
                    return
                # Don't advance hang timer while offer WAV is still draining
                if self._idle_state == _IDLE_OFFERED and bridge is not None and bridge.is_tx_busy():
                    elapsed = self._silence_elapsed
                    state = self._idle_state
                else:
                    self._silence_elapsed += dt
                    elapsed = self._silence_elapsed
                    state = self._idle_state

            if now - last_log >= 3.0:
                print(
                    f"[ai] silence={elapsed:.1f}s state={state} "
                    f"(offer@{offer_after:.0f}s hang@{hang_after:.0f}s)",
                    flush=True,
                )
                last_log = now

            if state == _IDLE_ACTIVE and elapsed >= offer_after:
                print(f"[ai] mutual silence {elapsed:.0f}s — offering more help", flush=True)
                with self._lock:
                    self._idle_state = _IDLE_OFFERED
                    self._silence_elapsed = 0.0
                    self._idle_last_tick = time.time()
                offer, offer_wav = self._next_help_offer()
                print(f"[ai] help offer this time: {offer!r}", flush=True)
                self._play_phrase_or_speak(gemini, offer, offer_wav, kind="help_offer")
            elif state == _IDLE_OFFERED and elapsed >= hang_after:
                print(f"[ai] still silent {elapsed:.0f}s — idle farewell + hang up", flush=True)
                line, wav = self._next_idle_farewell()
                with self._lock:
                    self._idle_state = _IDLE_ENDING
                print(f"[ai] idle farewell this time: {line!r}", flush=True)
                gemini.ignore_hangup_signals = True
                used_wav = self._play_phrase_or_speak(
                    gemini, line, wav, kind="idle_farewell"
                )
                threading.Thread(
                    target=self._idle_farewell_then_hangup,
                    kwargs={"prerecorded": used_wav},
                    name="idle-hangup",
                    daemon=True,
                ).start()
                return

    def _idle_farewell_then_hangup(self, prerecorded: bool = False) -> None:
        """Wait until idle farewell is spoken on the phone, then SIP BYE."""
        bridge = self._bridge
        gemini = self._gemini
        if prerecorded:
            # WAV is already queued — just drain it.
            if bridge is not None:
                bridge.wait_tx_idle(timeout=12.0)
            time.sleep(0.15)
        else:
            started = False
            deadline = time.time() + 5.0
            while time.time() < deadline and not self._hanging_up:
                if bridge is not None and bridge.is_tx_busy():
                    started = True
                    break
                time.sleep(0.05)
            if not started:
                print("[ai] idle farewell audio did not start — hanging up anyway", flush=True)
            else:
                print("[ai] idle farewell playing — waiting for end", flush=True)
                if bridge is not None:
                    bridge.wait_tx_idle(timeout=12.0)
                time.sleep(0.25)
        if gemini is not None:
            gemini.ignore_hangup_signals = False
        self.request_hang_up()

    def request_hang_up(self) -> None:
        """Let farewell audio finish, then send SIP BYE."""
        with self._lock:
            if self._hanging_up:
                return
            self._hanging_up = True
            self._idle_state = _IDLE_ENDING
            self._stop_idle_unlocked()
            bridge = self._bridge
            pbx = self.pbx

        print("[ai] farewell — draining audio, then hanging up", flush=True)
        if bridge is not None:
            # Finish whatever farewell is already queued, then BYE immediately.
            bridge.wait_tx_idle(timeout=3.0)
        if pbx is not None:
            pbx.hangup()
        else:
            print("[ai] hang up requested but PBX not attached", flush=True)

    def on_ended(self) -> None:
        with self._lock:
            self._stop_idle_unlocked()
            self._teardown_unlocked()
            self._hanging_up = False
            self._idle_state = _IDLE_ACTIVE
        print("[ai] call media stopped — ready for next call", flush=True)

    def _teardown_unlocked(self) -> None:
        if self._gemini:
            self._gemini.close()
            self._gemini = None
        if self._bridge:
            self._bridge.stop()
            self._bridge = None


def main() -> int:
    load_dotenv(env_path(), override=True)

    parser = argparse.ArgumentParser(description="AI voice phone via Gemini Live")
    parser.add_argument(
        "--bind",
        default=os.environ.get("BIND_IP", config.BIND_IP).strip() or config.BIND_IP,
        help="SIP bind IP (0.0.0.0 = all interfaces); or set BIND_IP in .env",
    )
    parser.add_argument("--port", type=int, default=config.SIP_PORT)
    parser.add_argument("--ext", default=config.PHONE_EXTENSION)
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for phone REGISTER",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_LIVE_MODEL", config.GEMINI_LIVE_MODEL),
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="do not register Windows login auto-start (frozen .exe only)",
    )
    parser.add_argument(
        "--uninstall-autostart",
        action="store_true",
        help="remove Windows login auto-start and exit",
    )
    args = parser.parse_args()

    if args.uninstall_autostart:
        remove_autostart()
        return 0
    if not args.no_autostart:
        ensure_autostart()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("Missing GEMINI_API_KEY.")
        print(f"Create {env_path()} with GEMINI_API_KEY=... from https://aistudio.google.com/apikey")
        return 1

    controller = AiCallController(api_key=api_key, model=args.model, bind_ip=args.bind)

    pbx = SipPbx(
        bind_ip=args.bind,
        sip_port=args.port,
        accounts=config.ACCOUNTS,
        rtp_port=config.RTP_PORT,
        auto_answer=True,
        auto_answer_destinations={config.AI_EXTENSION},
        auto_answer_delay=config.AI_RING_SECONDS,
        play_tone_on_answer=False,
        on_call_answered=controller.on_answered,
        on_call_ended=controller.on_ended,
    )
    controller.attach_pbx(pbx)

    try:
        pbx.start()
    except OSError as exc:
        print(f"Could not bind {args.bind}:{args.port}: {exc}")
        print("Stop any other sip_pbx.py / interactive.py / call.py process first.")
        return 1

    print()
    print("Waiting for desk phone REGISTER...")
    print(f"  SIP server : {args.bind}:{args.port}")
    print(f"  phone ext  : {args.ext}  (password {config.ACCOUNTS[args.ext]})")
    print(f"  AI ext     : {config.AI_EXTENSION}  ← dial this to talk")
    print(f"  Gemini     : {args.model}")
    print()
    print("Hot Line tip (KX-HDV130): set Hot Line number to 100")
    print("so lifting the handset calls the AI. Or dial 100 after pickup.")
    print()

    if not pbx.wait_for_register(args.ext, timeout=args.timeout):
        print("Timed out waiting for REGISTER. Is the phone pointed at this PC?")
        print(f"Phone SIP user/pass should be {args.ext}/{config.ACCOUNTS[args.ext]}")
        pbx.stop()
        return 2

    print(f"Phone {args.ext} registered. Dial {config.AI_EXTENSION} for AI.")
    print("Ctrl+C to stop.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        controller.on_ended()
        pbx.stop()
        print("bye")

    return 0


if __name__ == "__main__":
    sys.exit(main())
