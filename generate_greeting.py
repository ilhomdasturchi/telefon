"""
Generate pre-recorded Uzbek greeting WAVs.

Default engine is local MMS Uzbek TTS (natural, no API quota).

  python generate_greeting.py
  python generate_greeting.py --force
  python generate_greeting.py --engine edge --force
  python generate_greeting.py --engine gemini --force
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import config

# Order matters: digraphs before single letters.
_LAT2CYR = [
    ("sh", "ш"),
    ("ch", "ч"),
    ("ng", "нг"),
    ("g'", "ғ"),
    ("g‘", "ғ"),
    ("g`", "ғ"),
    ("o'", "ў"),
    ("o‘", "ў"),
    ("o`", "ў"),
    ("yo", "ё"),
    ("yu", "ю"),
    ("ya", "я"),
    ("ye", "е"),
    ("ts", "ц"),
    ("a", "а"),
    ("b", "б"),
    ("v", "в"),
    ("g", "г"),
    ("d", "д"),
    ("e", "е"),
    ("z", "з"),
    ("i", "и"),
    ("y", "й"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("f", "ф"),
    ("x", "х"),
    ("q", "қ"),
    ("h", "ҳ"),
    ("j", "ж"),
    ("'", ""),
    ("‘", ""),
    ("`", ""),
    (" ", " "),
    ("?", "?"),
    ("!", "!"),
    (",", ","),
    (".", "."),
]


def latin_to_cyrillic(text: str) -> str:
    text = text.lower().replace("ʼ", "'").replace("ʻ", "'")
    out: list[str] = []
    i = 0
    while i < len(text):
        matched = False
        # Word-initial e → э (Uzbek), not е
        if text.startswith("e", i) and (i == 0 or not text[i - 1].isalpha()):
            out.append("э")
            i += 1
            continue
        for lat, cyr in _LAT2CYR:
            if text.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                matched = True
                break
        if not matched:
            out.append(text[i])
            i += 1
    return "".join(out)


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def mp3_to_wav_pcm24k(mp3_bytes: bytes) -> bytes:
    """Convert MP3 bytes to mono 24 kHz 16-bit WAV bytes via bundled ffmpeg."""
    ffmpeg = _ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        mp3_path = Path(tmp) / "in.mp3"
        wav_path = Path(tmp) / "out.wav"
        mp3_path.write_bytes(mp3_bytes)
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(mp3_path),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-sample_fmt",
            "s16",
            str(wav_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not wav_path.is_file():
            raise RuntimeError(f"ffmpeg failed: {proc.stderr[-500:]}")
        return wav_path.read_bytes()


def pcm_float_to_wav(pcm, sample_rate: int) -> bytes:
    import numpy as np

    audio = np.asarray(pcm, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-6:
        audio = audio / peak * 0.92
    pcm16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


_mms_model = None
_mms_tokenizer = None


def _load_mms():
    global _mms_model, _mms_tokenizer
    if _mms_model is not None:
        return _mms_model, _mms_tokenizer

    import torch
    from transformers import AutoTokenizer, VitsModel

    repo = getattr(
        config,
        "MMS_TTS_MODEL",
        "MuzaffarSharofitdinov/mms-tts-uzbek-girl-voice_cyrillic",
    )
    print(f"  loading MMS model {repo} ...", flush=True)
    _mms_tokenizer = AutoTokenizer.from_pretrained(repo)
    _mms_model = VitsModel.from_pretrained(repo)
    _mms_model.eval()
    return _mms_model, _mms_tokenizer


def synthesize_mms(speak: str) -> bytes:
    import torch

    model, tokenizer = _load_mms()
    cyr = latin_to_cyrillic(speak)
    try:
        print(f"     cyr={cyr!r}", flush=True)
    except UnicodeEncodeError:
        print(f"     cyr=<{len(cyr)} chars>", flush=True)
    inputs = tokenizer(cyr, return_tensors="pt")
    length_scale = float(getattr(config, "MMS_LENGTH_SCALE", 1.15))
    noise_scale = float(getattr(config, "MMS_NOISE_SCALE", 0.333))
    with torch.no_grad():
        try:
            wave_t = model(
                **inputs,
                noise_scale=noise_scale,
                noise_scale_w=0.8,
                length_scale=length_scale,
            ).waveform
        except TypeError:
            wave_t = model(**inputs).waveform
    audio = wave_t.squeeze().detach().cpu().numpy()
    rate = int(getattr(model.config, "sampling_rate", 16000))
    return pcm_float_to_wav(audio, rate)


async def synthesize_edge(speak: str, voice: str, rate: str, pitch: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(speak, voice=voice, rate=rate, pitch=pitch)
    mp3 = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    if not mp3:
        raise RuntimeError("edge-tts returned no audio")
    return mp3_to_wav_pcm24k(bytes(mp3))


def synthesize_gemini(speak: str, style: str) -> bytes:
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
    response = client.models.generate_content(
        model=config.GREETING_TTS_MODEL,
        contents=[
            {
                "role": "user",
                "parts": [{"text": speak, "speech_metadata": {"style": style}}],
            }
        ],
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.GREETING_VOICE,
                    )
                )
            ),
        ),
    )
    part = response.candidates[0].content.parts[0]
    data = part.inline_data.data
    mime = part.inline_data.mime_type or ""
    if isinstance(data, str):
        import base64

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate unlocked greetings even if files exist",
    )
    parser.add_argument(
        "--engine",
        choices=("mms", "edge", "gemini"),
        default=os.environ.get(
            "GREETING_TTS_ENGINE", getattr(config, "GREETING_TTS_ENGINE", "mms")
        ),
        help="TTS backend (default: mms — local Uzbek, no API quota)",
    )
    args = parser.parse_args()

    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass

    out_dir = Path(config.GREETING_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    voice = getattr(config, "EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")
    rate = getattr(config, "EDGE_TTS_RATE", "-5%")
    pitch = getattr(config, "EDGE_TTS_PITCH", "-2Hz")

    print(f"engine={args.engine}")
    if args.engine == "edge":
        print(f"voice={voice} rate={rate} pitch={pitch}")
    elif args.engine == "gemini":
        print(f"model={config.GREETING_TTS_MODEL} voice={config.GREETING_VOICE}")
    else:
        print(
            f"model={getattr(config, 'MMS_TTS_MODEL', 'MuzaffarSharofitdinov/mms-tts-uzbek-girl-voice_cyrillic')}"
        )

    for item in config.GREETINGS:
        out = out_dir / f"{item['id']}.wav"
        if item.get("lock"):
            print(f"  lock  {item['label']!r} ({out.name}) — kept")
            continue
        if out.is_file() and out.stat().st_size > 1000 and not args.force:
            print(f"  skip  {item['label']!r} (exists; use --force)")
            continue

        print(f"  -> {item['label']!r}  speak={item['speak']!r}")
        for attempt in range(1, 6):
            try:
                if args.engine == "mms":
                    wav = synthesize_mms(item["speak"])
                elif args.engine == "edge":
                    wav = asyncio.run(synthesize_edge(item["speak"], voice, rate, pitch))
                else:
                    wav = synthesize_gemini(item["speak"], item["style"])
                out.write_bytes(wav)
                print(f"     wrote {out.stat().st_size} bytes")
                break
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if args.engine == "gemini" and ("429" in msg or "RESOURCE_EXHAUSTED" in msg):
                    wait = 45 * attempt
                    print(f"     rate limited, wait {wait}s (attempt {attempt}/5)...")
                    time.sleep(wait)
                    continue
                print(f"     error: {exc}")
                if attempt >= 5:
                    return 1
                time.sleep(2)
        else:
            print(f"     FAILED {item['label']!r}")
            return 1

        if args.engine == "gemini":
            time.sleep(25)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
