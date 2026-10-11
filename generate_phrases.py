"""
Generate pre-recorded call phrase WAVs.

TTS fallback chain:
  1. edge (Microsoft Edge Read Aloud, free)
  2. gemini-3.1-flash-tts-preview
  3. gemini-2.5-flash-preview-tts
  4. gemini-3.8-flash-lite-tts
  5. mms (local Uzbek)

  python generate_phrases.py
  python generate_phrases.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import sys
import time
import wave
from pathlib import Path

import config

# Preferred order — 3.1 first, then others if rate-limited
_DEFAULT_GEMINI_MODELS = (
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-3.8-flash-lite-tts",
)


def _is_rate_limited(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()


def synthesize_gemini(speak: str, style: str, model: str, voice: str) -> bytes:
    import truststore

    truststore.inject_into_ssl()
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in .env")

    client = genai.Client(api_key=api_key)
    # IMPORTANT: put ONLY the Uzbek phrase in `text`. Never put English style
    # instructions in the spoken text — TTS will read them aloud.
    speech_cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                {
                    "role": "user",
                    "parts": [{"text": speak, "speech_metadata": {"style": style}}],
                }
            ],
            config=speech_cfg,
        )
    except Exception as exc:
        # gemini-3.1-flash-tts-preview rejects speech_metadata
        if "Speech metadata" not in str(exc) and "speech_metadata" not in str(exc).lower():
            raise
        response = client.models.generate_content(
            model=model,
            contents=speak,
            config=speech_cfg,
        )
    cand = response.candidates
    if not cand or not cand[0].content or not cand[0].content.parts:
        raise RuntimeError(f"{model} returned empty audio")
    part = cand[0].content.parts[0]
    inline = part.inline_data
    if not inline or not inline.data:
        raise RuntimeError(f"{model} returned no inline audio")
    data = inline.data
    mime = inline.mime_type or ""
    if isinstance(data, str):
        data = base64.b64decode(data)
    if mime.startswith("audio/wav") or (len(data) >= 4 and data[:4] == b"RIFF"):
        return data

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(data)
    return buf.getvalue()


def synthesize_edge(speak: str) -> bytes:
    from generate_greeting import synthesize_edge as _edge

    voice = getattr(config, "EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")
    rate = getattr(config, "EDGE_TTS_RATE", "-5%")
    pitch = getattr(config, "EDGE_TTS_PITCH", "-2Hz")
    return asyncio.run(_edge(speak, voice, rate, pitch))


def synthesize_mms(speak: str) -> bytes:
    from generate_greeting import synthesize_mms

    return synthesize_mms(speak)


def _gemini_model_list() -> list[str]:
    primary = getattr(config, "PHRASE_TTS_MODEL", None)
    models: list[str] = []
    if primary:
        models.append(primary)
    for m in _DEFAULT_GEMINI_MODELS:
        if m not in models:
            models.append(m)
    return models


def synthesize_with_fallback(
    speak: str,
    style: str,
    voice: str,
    preferred_engine: str | None,
) -> tuple[bytes, str]:
    """Return (wav_bytes, engine_label). Updates preferred_engine via list trick."""
    engines: list[str] = ["edge"]
    for m in _gemini_model_list():
        engines.append(f"gemini:{m}")
    engines.append("mms")

    # Rotate so we keep using the last successful non-rate-limited engine
    if preferred_engine and preferred_engine in engines:
        idx = engines.index(preferred_engine)
        engines = engines[idx:] + engines[:idx]

    last_err: BaseException | None = None
    for eng in engines:
        try:
            if eng.startswith("gemini:"):
                model = eng.split(":", 1)[1]
                print(f"     try {eng} ...", flush=True)
                wav = synthesize_gemini(speak, style, model, voice)
                return wav, eng
            if eng == "edge":
                print("     try edge-tts ...", flush=True)
                return synthesize_edge(speak), "edge"
            if eng == "mms":
                print("     try mms (local) ...", flush=True)
                return synthesize_mms(speak), "mms"
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if _is_rate_limited(exc):
                print(f"     rate-limited on {eng}, next engine...", flush=True)
                continue
            print(f"     {eng} failed: {exc}", flush=True)
            # For Gemini not-found, try next; for others also continue
            continue
    raise RuntimeError(f"all TTS engines failed; last error: {last_err}")


def _all_phrases() -> list[tuple[str, dict]]:
    groups = (
        ("farewell", getattr(config, "FAREWELL_PHRASES", ())),
        ("help_offer", getattr(config, "HELP_OFFER_PHRASES", ())),
        ("idle_farewell", getattr(config, "IDLE_FAREWELL_PHRASES", ())),
    )
    out: list[tuple[str, dict]] = []
    for group, items in groups:
        for item in items:
            if isinstance(item, dict):
                out.append((group, item))
            else:
                slug = "".join(c if c.isalnum() else "_" for c in str(item).lower())[:40]
                out.append(
                    (
                        group,
                        {
                            "id": slug.strip("_") or "phrase",
                            "speak": str(item),
                            "style": "Calm Uzbek phone voice.",
                        },
                    )
                )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    voice = getattr(config, "PHRASE_TTS_VOICE", "Kore")
    out_dir = Path(getattr(config, "PHRASE_DIR", "assets/phrases"))
    out_dir.mkdir(parents=True, exist_ok=True)

    print("phrase TTS fallback chain:")
    print("  - edge")
    for m in _gemini_model_list():
        print(f"  - gemini:{m}")
    print("  - mms")
    print(f"out={out_dir}")

    preferred: str | None = "edge"

    for group, item in _all_phrases():
        out = out_dir / f"{item['id']}.wav"
        speak = item["speak"]
        style = item.get("style") or "Calm Uzbek phone voice."
        if out.is_file() and out.stat().st_size > 1000 and not args.force:
            print(f"  skip  [{group}] {speak!r}")
            continue
        print(f"  -> [{group}] {speak!r}")
        try:
            wav, eng = synthesize_with_fallback(speak, style, voice, preferred)
            preferred = eng
            out.write_bytes(wav)
            print(f"     wrote {out.stat().st_size} bytes via {eng}")
        except Exception as exc:  # noqa: BLE001
            print(f"     FAILED: {exc}")
            return 1
        # Small pause only for Gemini to avoid RPM spikes
        if preferred and preferred.startswith("gemini:"):
            time.sleep(3)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
