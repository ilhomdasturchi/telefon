"""Load and play pre-recorded greeting / call phrases over the RTP bridge."""

from __future__ import annotations

import random
import wave
from pathlib import Path

import config
from audio_codec import resample_pcm16
from rtp_bridge import GEMINI_OUT_RATE, RtpBridge


def _phrase_item_id(item) -> str:
    if isinstance(item, dict):
        return str(item["id"])
    slug = "".join(c if c.isalnum() else "_" for c in str(item).lower())[:40]
    return slug.strip("_") or "phrase"


def _phrase_item_speak(item) -> str:
    if isinstance(item, dict):
        return str(item["speak"])
    return str(item)


def phrase_wav_path(item) -> Path:
    root = Path(getattr(config, "PHRASE_DIR", Path(config.GREETING_DIR).parent / "phrases"))
    return root / f"{_phrase_item_id(item)}.wav"


def pick_random_phrase(
    items,
    *,
    exclude_speak: str | None = None,
) -> tuple[str, Path | None]:
    """Return (speak_text, wav_path_or_None). Prefers not repeating exclude_speak."""
    seq = tuple(items or ())
    if not seq:
        return "", None
    if exclude_speak and len(seq) > 1:
        others = [i for i in seq if _phrase_item_speak(i) != exclude_speak]
        if others:
            seq = tuple(others)
    item = random.choice(seq)
    path = phrase_wav_path(item)
    return _phrase_item_speak(item), (path if path.is_file() else None)


def greeting_paths() -> list[Path]:
    """Configured greeting WAVs; fall back to any .wav in the greetings dir."""
    root = Path(config.GREETING_DIR)
    paths = [root / f"{item['id']}.wav" for item in config.GREETINGS]
    existing = [p for p in paths if p.is_file()]
    if existing:
        return existing
    # ignore old unused files like uz_hello.wav if configs missing
    return sorted(p for p in root.glob("*.wav") if p.name != "uz_hello.wav")


def pick_random_greeting() -> Path | None:
    paths = greeting_paths()
    if not paths:
        return None
    return random.choice(paths)


def load_wav_pcm16(path: str | Path) -> tuple[bytes, int]:
    """Return (pcm16_mono_le, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"unsupported sample width {width} (need 16-bit)")
    if channels == 2:
        import struct

        samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
        mono = bytearray()
        for i in range(0, len(samples), 2):
            v = (samples[i] + samples[i + 1]) // 2
            mono += struct.pack("<h", v)
        frames = bytes(mono)
    elif channels != 1:
        raise ValueError(f"unsupported channels {channels}")
    return frames, rate


def play_wav(bridge: RtpBridge, path: str | Path, *, label: str = "audio") -> None:
    """Push WAV audio into the bridge TX path (Gemini 24 kHz PCM queue)."""
    pcm, rate = load_wav_pcm16(path)
    if rate != GEMINI_OUT_RATE:
        pcm = resample_pcm16(pcm, rate, GEMINI_OUT_RATE)
    chunk = 1920  # ~40ms @ 24kHz
    for i in range(0, len(pcm), chunk):
        bridge.push_gemini_pcm24k(pcm[i : i + chunk])
    print(f"[{label}] queued {Path(path).name} ({len(pcm)} bytes)", flush=True)


def play_greeting_wav(bridge: RtpBridge, path: str | Path) -> None:
    play_wav(bridge, path, label="greeting")


def play_random_greeting(bridge: RtpBridge) -> bool:
    path = pick_random_greeting()
    if not path:
        return False
    play_greeting_wav(bridge, path)
    return True
