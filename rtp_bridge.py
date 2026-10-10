"""
Bidirectional RTP bridge for phone PCMU <-> Gemini PCM.

Phone side: G.711 µ-law @ 8 kHz
Gemini in:  PCM16 LE @ 16 kHz
Gemini out: PCM16 LE @ 24 kHz
"""

from __future__ import annotations

import queue
import random
import socket
import struct
import threading
import time
from typing import Callable, Optional

import config
from audio_codec import linear16_to_pcmu, pcma_to_linear16, pcmu_to_linear16, resample_pcm16


def _apply_gain_pcm16(pcm: bytes, gain: float) -> bytes:
    if gain == 1.0 or not pcm:
        return pcm
    out = bytearray(len(pcm))
    for i in range(0, len(pcm), 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        boosted = int(sample * gain)
        if boosted > 32767:
            boosted = 32767
        elif boosted < -32768:
            boosted = -32768
        struct.pack_into("<h", out, i, boosted)
    return bytes(out)

PHONE_RATE = 8000
GEMINI_IN_RATE = 16000
GEMINI_OUT_RATE = 24000
FRAME_SAMPLES = 160  # 20 ms @ 8 kHz
FRAME_BYTES_PCMU = FRAME_SAMPLES


class RtpBridge:
    def __init__(
        self,
        bind_ip: str,
        local_port: int,
        remote: tuple[str, int],
        on_pcm16_16k: Optional[Callable[[bytes], None]] = None,
    ):
        self.bind_ip = bind_ip
        self.local_port = local_port
        self.remote = remote
        self.on_pcm16_16k = on_pcm16_16k

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stop = threading.Event()
        self._tx_q: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=100)
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._out_pcm_buf = bytearray()
        self._lock = threading.Lock()
        self._last_push_at = 0.0
        self._last_tx_frame_at = 0.0

        self._ssrc = random.randint(1, 0xFFFFFFFF)
        self._seq = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)
        self._rx_packets = 0
        self._rx_datagrams = 0
        self._tx_packets = 0

    def start(self) -> None:
        self._sock.bind((self.bind_ip, self.local_port))
        self._stop.clear()
        self._rx_packets = 0
        self._rx_datagrams = 0
        self._tx_packets = 0
        self._rx_thread = threading.Thread(target=self._rx_loop, name="rtp-rx", daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, name="rtp-tx", daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()
        print(
            f"[rtp] bridge {self.bind_ip}:{self.local_port} <-> {self.remote[0]}:{self.remote[1]}",
            flush=True,
        )

    @staticmethod
    def _rtp_payload(packet: bytes) -> Optional[tuple[int, bytes]]:
        if len(packet) < 12:
            return None
        b0, b1 = packet[0], packet[1]
        if (b0 >> 6) != 2:
            return None
        cc = b0 & 0x0F
        has_ext = bool(b0 & 0x10)
        padding = bool(b0 & 0x20)
        pt = b1 & 0x7F
        offset = 12 + 4 * cc
        if has_ext:
            if len(packet) < offset + 4:
                return None
            ext_len = struct.unpack_from("!H", packet, offset + 2)[0]
            offset += 4 + 4 * ext_len
        if offset > len(packet):
            return None
        payload = packet[offset:]
        if padding and payload:
            pad = payload[-1]
            if pad <= len(payload):
                payload = payload[:-pad]
        return pt, payload

    def stop(self) -> None:
        self._stop.set()
        try:
            self._tx_q.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        if self._rx_thread:
            self._rx_thread.join(timeout=2)
        if self._tx_thread:
            self._tx_thread.join(timeout=2)

    def push_gemini_pcm24k(self, pcm24k: bytes) -> None:
        if not pcm24k or self._stop.is_set():
            return
        self._last_push_at = time.time()
        try:
            self._tx_q.put_nowait(pcm24k)
        except queue.Full:
            try:
                self._tx_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._tx_q.put_nowait(pcm24k)
            except queue.Full:
                pass

    def flush_output(self) -> None:
        """Drop queued Gemini audio (barge-in)."""
        with self._lock:
            self._out_pcm_buf.clear()
        while True:
            try:
                self._tx_q.get_nowait()
            except queue.Empty:
                break

    def is_tx_busy(self) -> bool:
        """True while AI audio is still being sent to the phone.

        Incomplete sub-frame leftovers in the PCM buffer are ignored — those used
        to stick forever and freeze the silence clock as 'AI speaking'.
        """
        if not self._tx_q.empty():
            return True
        with self._lock:
            # At least one full 20ms frame still waiting to send
            if len(self._out_pcm_buf) >= FRAME_SAMPLES * 2:
                return True
        # Recently pushed audio that may still be draining (~300ms grace)
        if self._last_push_at and (time.time() - self._last_push_at) < 0.35:
            return True
        if self._last_tx_frame_at and (time.time() - self._last_tx_frame_at) < 0.08:
            return True
        return False

    def wait_tx_idle(self, timeout: float = 30.0) -> bool:
        """Wait until outbound audio queue/buffer has finished playing."""
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            if not self.is_tx_busy():
                # brief settle so the last paced frame is sent
                time.sleep(0.02)
                if not self.is_tx_busy():
                    return True
            time.sleep(0.01)
        return False

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sock.settimeout(0.5)
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._rx_datagrams += 1
            parsed = self._rtp_payload(data)
            if not parsed:
                if self._rx_datagrams <= 5:
                    print(
                        f"[rtp] rx datagram #{self._rx_datagrams} from {addr[0]}:{addr[1]} "
                        f"len={len(data)} (not RTP / parse failed)",
                        flush=True,
                    )
                continue
            pt, payload = parsed
            # Learn actual RTP source (NAT / SDP mismatch)
            if addr[0]:
                self.remote = addr
            # PT 0 = PCMU, PT 8 = PCMA; ignore DTMF telephone-event (usually 101)
            if pt == 0 and payload:
                pcm8 = pcmu_to_linear16(payload)
            elif pt == 8 and payload:
                pcm8 = pcma_to_linear16(payload)
            else:
                if self._rx_datagrams <= 8:
                    print(
                        f"[rtp] rx ignore from {addr[0]}:{addr[1]} pt={pt} len={len(payload)} "
                        f"(want PT 0/8 PCMU/PCMA)",
                        flush=True,
                    )
                continue
            self._rx_packets += 1
            if self._rx_packets in {1, 50, 250}:
                print(
                    f"[rtp] rx #{self._rx_packets} from {addr[0]}:{addr[1]} pt={pt} len={len(payload)}",
                    flush=True,
                )
            pcm16 = resample_pcm16(pcm8, PHONE_RATE, GEMINI_IN_RATE)
            pcm16 = _apply_gain_pcm16(pcm16, getattr(config, "MIC_GAIN", 1.0))
            if self.on_pcm16_16k and pcm16:
                try:
                    self.on_pcm16_16k(pcm16)
                except Exception as exc:  # noqa: BLE001
                    print(f"[rtp] on_pcm16_16k error: {exc}", flush=True)

    def _tx_loop(self) -> None:
        # Pace TX at 20 ms frames
        next_deadline = time.perf_counter()
        while not self._stop.is_set():
            try:
                chunk = self._tx_q.get(timeout=0.05)
            except queue.Empty:
                chunk = b""
                # Drop incomplete leftover samples when the queue is idle so
                # is_tx_busy() cannot stick true forever after a short utterance.
                with self._lock:
                    if 0 < len(self._out_pcm_buf) < FRAME_SAMPLES * 2:
                        self._out_pcm_buf.clear()
            if chunk is None:
                break
            if chunk:
                pcm8 = resample_pcm16(chunk, GEMINI_OUT_RATE, PHONE_RATE)
                with self._lock:
                    self._out_pcm_buf.extend(pcm8)

            while True:
                with self._lock:
                    if len(self._out_pcm_buf) < FRAME_SAMPLES * 2:
                        break
                    frame_pcm = bytes(self._out_pcm_buf[: FRAME_SAMPLES * 2])
                    del self._out_pcm_buf[: FRAME_SAMPLES * 2]
                pcmu = linear16_to_pcmu(frame_pcm)
                header = struct.pack(
                    "!BBHII",
                    0x80,
                    0x00,  # PT=0 PCMU
                    self._seq & 0xFFFF,
                    self._timestamp & 0xFFFFFFFF,
                    self._ssrc,
                )
                try:
                    self._sock.sendto(header + pcmu, self.remote)
                except OSError:
                    return
                self._tx_packets += 1
                self._last_tx_frame_at = time.time()
                if self._tx_packets in {1, 50, 250}:
                    print(
                        f"[rtp] tx #{self._tx_packets} to {self.remote[0]}:{self.remote[1]}",
                        flush=True,
                    )
                self._seq = (self._seq + 1) & 0xFFFF
                self._timestamp = (self._timestamp + FRAME_SAMPLES) & 0xFFFFFFFF
                next_deadline += 0.02
                delay = next_deadline - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_deadline = time.perf_counter()
