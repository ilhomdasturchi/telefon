"""Minimal SIP UAC: REGISTER as ext, INVITE 100, send PCMU RTP, hang up.

Usage:
  python sip_uac_diag.py --server 192.168.1.200 --user 105 --bind 192.168.1.100
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import socket
import struct
import time
import uuid


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def parse_headers(raw: bytes) -> tuple[str, dict[str, str], str]:
    text = raw.decode("utf-8", errors="replace")
    if "\r\n\r\n" in text:
        head, body = text.split("\r\n\r\n", 1)
    else:
        head, body = text, ""
    lines = head.split("\r\n")
    start = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return start, headers, body


def parse_auth(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if value.lower().startswith("digest "):
        value = value[7:]
    for part in value.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip().lower()] = v.strip().strip('"')
    return out


def digest_response(
    user: str,
    password: str,
    method: str,
    uri: str,
    auth: dict[str, str],
) -> str:
    realm = auth["realm"]
    nonce = auth["nonce"]
    ha1 = md5(f"{user}:{realm}:{password}")
    ha2 = md5(f"{method}:{uri}")
    qop = auth.get("qop")
    if qop == "auth":
        nc = "00000001"
        cnonce = uuid.uuid4().hex[:16]
        resp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        return (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{resp}", algorithm=MD5, qop=auth, '
            f'nc={nc}, cnonce="{cnonce}"'
        )
    resp = md5(f"{ha1}:{nonce}:{ha2}")
    return (
        f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{resp}", algorithm=MD5'
    )


def make_pcmu_tone(seconds: float = 1.0, freq: float = 700.0) -> bytes:
    n = int(seconds * 8000)
    pcm = bytearray()
    for i in range(n):
        sample = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / 8000))
        pcm += struct.pack("<h", sample)
    # µ-law encode (reuse simple path via audio_codec if available)
    try:
        from audio_codec import linear16_to_pcmu

        return linear16_to_pcmu(bytes(pcm))
    except Exception:
        # fallback silence PCMU
        return bytes([0xFF] * n)


def recv_until(
    sock: socket.socket,
    predicate,
    timeout: float = 5.0,
) -> tuple[str, dict[str, str], str, tuple[str, int]] | None:
    sock.settimeout(0.5)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        start, headers, body = parse_headers(data)
        print(f"<< {start} from {addr[0]}:{addr[1]}", flush=True)
        if predicate(start, headers, body):
            return start, headers, body, addr
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="192.168.1.200")
    ap.add_argument("--sport", type=int, default=5060)
    ap.add_argument("--user", default="105")
    ap.add_argument("--password", default="")
    ap.add_argument("--bind", default="192.168.1.100")
    ap.add_argument("--lport", type=int, default=5062)
    ap.add_argument("--dest", default="100")
    ap.add_argument("--talk", type=float, default=8.0, help="seconds of RTP to send")
    args = ap.parse_args()
    password = args.password or args.user
    server = (args.server, args.sport)
    local_ip = args.bind
    local_sip = args.lport
    local_rtp = 16000 + random.randint(0, 200)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind all interfaces; advertise local_ip in Contact/SDP (binding only
    # 192.168.1.100 can miss replies on some Windows setups).
    sock.bind(("0.0.0.0", local_sip))
    local_sip = sock.getsockname()[1]
    print(f"[uac] bound sip 0.0.0.0:{local_sip} (advertise {local_ip})", flush=True)

    def send(msg: str) -> None:
        print(f">> {msg.splitlines()[0]}", flush=True)
        sock.sendto(msg.encode("utf-8"), server)

    # --- REGISTER ---
    call_id = uuid.uuid4().hex
    from_tag = uuid.uuid4().hex[:8]
    branch = f"z9hG4bK{uuid.uuid4().hex}"
    uri = f"sip:{args.server}"
    contact = f"<sip:{args.user}@{local_ip}:{local_sip}>"

    def register(auth_header: str | None = None) -> None:
        cseq = 1 if not auth_header else 2
        lines = [
            f"REGISTER {uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {local_ip}:{local_sip};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <sip:{args.user}@{args.server}>;tag={from_tag}",
            f"To: <sip:{args.user}@{args.server}>",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} REGISTER",
            f"Contact: {contact}",
            "Expires: 120",
            "User-Agent: telefon-uac-diag/1.0",
        ]
        if auth_header:
            lines.append(f"Authorization: {auth_header}")
        lines.append("Content-Length: 0")
        send("\r\n".join(lines) + "\r\n\r\n")

    register()
    got = recv_until(sock, lambda s, h, b: s.startswith("SIP/2.0"), timeout=5)
    if not got:
        print("[uac] REGISTER timeout — is AiPhone running on server?")
        return 2
    start, headers, body, _ = got
    if "401" in start:
        auth = parse_auth(headers.get("www-authenticate", ""))
        ah = digest_response(args.user, password, "REGISTER", uri, auth)
        branch = f"z9hG4bK{uuid.uuid4().hex}"
        register(ah)
        got = recv_until(sock, lambda s, h, b: s.startswith("SIP/2.0"), timeout=5)
        if not got:
            print("[uac] REGISTER auth timeout")
            return 2
        start, headers, body, _ = got
    if not start.startswith("SIP/2.0 200"):
        print(f"[uac] REGISTER failed: {start}")
        return 3
    print(f"[uac] REGISTERED as {args.user}", flush=True)

    # Bind RTP before INVITE so SDP port is real
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp.bind(("0.0.0.0", local_rtp))
    local_rtp = rtp.getsockname()[1]
    rtp.settimeout(0.05)
    print(f"[uac] bound rtp 0.0.0.0:{local_rtp} (advertise {local_ip})", flush=True)

    # --- INVITE 100 ---
    invite_id = uuid.uuid4().hex
    invite_tag = uuid.uuid4().hex[:8]
    invite_branch = f"z9hG4bK{uuid.uuid4().hex}"
    invite_uri = f"sip:{args.dest}@{args.server}"
    sdp = (
        "v=0\r\n"
        f"o={args.user} {int(time.time())} {int(time.time())} IN IP4 {local_ip}\r\n"
        "s=diag\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {local_rtp} RTP/AVP 0 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=sendrecv\r\n"
    )
    invite = "\r\n".join(
        [
            f"INVITE {invite_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {local_ip}:{local_sip};branch={invite_branch};rport",
            "Max-Forwards: 70",
            f'From: "{args.user}" <sip:{args.user}@{args.server}>;tag={invite_tag}',
            f"To: <sip:{args.dest}@{args.server}>",
            f"Call-ID: {invite_id}",
            "CSeq: 1 INVITE",
            f"Contact: {contact}",
            "Content-Type: application/sdp",
            f"Content-Length: {len(sdp.encode())}",
            "",
            sdp,
        ]
    )
    send(invite)

    # Collect provisional + 200
    to_tag = ""
    remote_rtp: tuple[str, int] | None = None
    deadline = time.time() + 20
    answered = False
    while time.time() < deadline and not answered:
        sock.settimeout(0.5)
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        start, headers, body = parse_headers(data)
        print(f"<< {start}", flush=True)
        if headers.get("call-id") != invite_id:
            continue
        if start.startswith("SIP/2.0 180") or start.startswith("SIP/2.0 183"):
            continue
        if start.startswith("SIP/2.0 200"):
            to_h = headers.get("to", "")
            if "tag=" in to_h:
                to_tag = to_h.split("tag=", 1)[1].split(";", 1)[0].strip()
            # parse SDP
            rip, rport = None, None
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("c=IN IP4 "):
                    rip = line.split()[-1]
                elif line.startswith("m=audio "):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        rport = int(parts[1])
            if rip and rport:
                remote_rtp = (rip, rport)
            # ACK
            ack = "\r\n".join(
                [
                    f"ACK {invite_uri} SIP/2.0",
                    f"Via: SIP/2.0/UDP {local_ip}:{local_sip};branch=z9hG4bK{uuid.uuid4().hex};rport",
                    "Max-Forwards: 70",
                    f'From: "{args.user}" <sip:{args.user}@{args.server}>;tag={invite_tag}',
                    f"To: <sip:{args.dest}@{args.server}>;tag={to_tag}",
                    f"Call-ID: {invite_id}",
                    "CSeq: 1 ACK",
                    "Content-Length: 0",
                    "",
                    "",
                ]
            )
            send(ack)
            answered = True
            break
        if start.startswith("SIP/2.0 4") or start.startswith("SIP/2.0 5") or start.startswith("SIP/2.0 6"):
            print(f"[uac] INVITE failed: {start}")
            return 4

    if not answered or not remote_rtp:
        print("[uac] no 200 OK / SDP from AI")
        return 5

    print(f"[uac] call active — sending PCMU RTP to {remote_rtp[0]}:{remote_rtp[1]} for {args.talk:.0f}s", flush=True)
    print(f"[uac] local RTP {local_ip}:{local_rtp} (server should TX here)", flush=True)

    tone = make_pcmu_tone(1.0)
    ssrc = random.randint(1, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    ts = random.randint(0, 0xFFFFFFFF)
    rx_count = 0
    tx_count = 0
    end = time.time() + args.talk
    frame = 160
    off = 0
    while time.time() < end:
        chunk = tone[off : off + frame]
        if len(chunk) < frame:
            off = 0
            chunk = tone[off : off + frame]
        off += frame
        header = struct.pack("!BBHII", 0x80, 0x00, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
        rtp.sendto(header + chunk, remote_rtp)
        seq += 1
        ts += frame
        tx_count += 1
        if tx_count in (1, 50, 200):
            print(f"[uac] rtp tx #{tx_count} -> {remote_rtp[0]}:{remote_rtp[1]}", flush=True)
        try:
            data, addr = rtp.recvfrom(2048)
            rx_count += 1
            if rx_count in (1, 50, 200):
                print(f"[uac] rtp rx #{rx_count} from {addr[0]}:{addr[1]} len={len(data)}", flush=True)
        except socket.timeout:
            pass
        time.sleep(0.02)

    print(f"[uac] done rtp tx={tx_count} rx={rx_count}", flush=True)

    # BYE
    bye = "\r\n".join(
        [
            f"BYE {invite_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {local_ip}:{local_sip};branch=z9hG4bK{uuid.uuid4().hex};rport",
            "Max-Forwards: 70",
            f'From: "{args.user}" <sip:{args.user}@{args.server}>;tag={invite_tag}',
            f"To: <sip:{args.dest}@{args.server}>;tag={to_tag}",
            f"Call-ID: {invite_id}",
            "CSeq: 2 BYE",
            "Content-Length: 0",
            "",
            "",
        ]
    )
    send(bye)
    recv_until(sock, lambda s, h, b: s.startswith("SIP/2.0"), timeout=3)

    rtp.close()
    sock.close()
    if tx_count > 0:
        print(
            "[uac] On the SERVER console you should now see [rtp] rx #1 from "
            f"{local_ip}:... — if not, inbound RTP is still blocked.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
