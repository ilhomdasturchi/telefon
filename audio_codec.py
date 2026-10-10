"""G.711 µ-law and simple PCM resampling helpers."""

from __future__ import annotations

import struct

_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635

# ITU-T G.711 µ-law decode table
_ULAW_DECODE = [
    -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
    -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
    -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
    -11900, -11388, -10876, -10364, -9852, -9340, -8828, -8316,
    -7932, -7676, -7420, -7164, -6908, -6652, -6396, -6140,
    -5884, -5628, -5372, -5116, -4860, -4604, -4348, -4092,
    -3900, -3772, -3644, -3516, -3388, -3260, -3132, -3004,
    -2876, -2748, -2620, -2492, -2364, -2236, -2108, -1980,
    -1884, -1820, -1756, -1692, -1628, -1564, -1500, -1436,
    -1372, -1308, -1244, -1180, -1116, -1052, -988, -924,
    -876, -844, -812, -780, -748, -716, -684, -652,
    -620, -588, -556, -524, -492, -460, -428, -396,
    -372, -356, -340, -324, -308, -292, -276, -260,
    -244, -228, -212, -196, -180, -164, -148, -132,
    -120, -112, -104, -96, -88, -80, -72, -64,
    -56, -48, -40, -32, -24, -16, -8, 0,
    32124, 31100, 30076, 29052, 28028, 27004, 25980, 24956,
    23932, 22908, 21884, 20860, 19836, 18812, 17788, 16764,
    15996, 15484, 14972, 14460, 13948, 13436, 12924, 12412,
    11900, 11388, 10876, 10364, 9852, 9340, 8828, 8316,
    7932, 7676, 7420, 7164, 6908, 6652, 6396, 6140,
    5884, 5628, 5372, 5116, 4860, 4604, 4348, 4092,
    3900, 3772, 3644, 3516, 3388, 3260, 3132, 3004,
    2876, 2748, 2620, 2492, 2364, 2236, 2108, 1980,
    1884, 1820, 1756, 1692, 1628, 1564, 1500, 1436,
    1372, 1308, 1244, 1180, 1116, 1052, 988, 924,
    876, 844, 812, 780, 748, 716, 684, 652,
    620, 588, 556, 524, 492, 460, 428, 396,
    372, 356, 340, 324, 308, 292, 276, 260,
    244, 228, 212, 196, 180, 164, 148, 132,
    120, 112, 104, 96, 88, 80, 72, 64,
    56, 48, 40, 32, 24, 16, 8, 0,
]


def linear16_to_pcmu(pcm: bytes) -> bytes:
    out = bytearray(len(pcm) // 2)
    for i in range(0, len(pcm), 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample
        if sample > _ULAW_CLIP:
            sample = _ULAW_CLIP
        sample += _ULAW_BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            mask >>= 1
            exponent -= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        out[i // 2] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(out)


def pcmu_to_linear16(pcmu: bytes) -> bytes:
    out = bytearray(len(pcmu) * 2)
    for i, b in enumerate(pcmu):
        struct.pack_into("<h", out, i * 2, _ULAW_DECODE[b])
    return bytes(out)


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear interpolation resampler for mono int16 PCM."""
    if src_rate == dst_rate or not pcm:
        return pcm
    n_src = len(pcm) // 2
    if n_src < 2:
        return pcm
    n_dst = max(1, int(round(n_src * dst_rate / src_rate)))
    out = bytearray(n_dst * 2)
    for i in range(n_dst):
        pos = i * (n_src - 1) / (n_dst - 1) if n_dst > 1 else 0.0
        i0 = int(pos)
        i1 = min(i0 + 1, n_src - 1)
        frac = pos - i0
        s0 = struct.unpack_from("<h", pcm, i0 * 2)[0]
        s1 = struct.unpack_from("<h", pcm, i1 * 2)[0]
        sample = int(s0 + (s1 - s0) * frac)
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        struct.pack_into("<h", out, i * 2, sample)
    return bytes(out)
