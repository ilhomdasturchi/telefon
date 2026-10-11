"""
Minimal SIP registrar + caller for a local IP phone.

1. Start this on your PC (default 192.168.1.100:5060).
2. Point the phone's SIP server at that IP, user/password 101/101 (AI is 100).
3. When REGISTER succeeds, press Enter (or run: python ring.py) to call it.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import socket
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import config
from audio_codec import linear16_to_pcmu


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _quote(value: str) -> str:
    return f'"{value}"'


@dataclass
class SipMessage:
    start_line: str
    headers: dict[str, str]
    body: str = ""
    raw: bytes = b""
    source: tuple[str, int] = ("", 0)

    @property
    def method(self) -> Optional[str]:
        if self.start_line.startswith("SIP/2.0"):
            return None
        return self.start_line.split(" ", 1)[0].upper()

    @property
    def status_code(self) -> Optional[int]:
        if not self.start_line.startswith("SIP/2.0"):
            return None
        parts = self.start_line.split(" ", 2)
        return int(parts[1]) if len(parts) >= 2 else None

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def via_branch(self) -> str:
        via = self.header("Via")
        for part in via.split(";"):
            part = part.strip()
            if part.lower().startswith("branch="):
                return part.split("=", 1)[1]
        return f"z9hG4bK{uuid.uuid4().hex}"


def parse_sip(data: bytes, source: tuple[str, int]) -> SipMessage:
    text = data.decode("utf-8", errors="replace")
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
        name, value = line.split(":", 1)
        key = name.strip().lower()
        value = value.strip()
        if key in headers:
            headers[key] = headers[key] + ", " + value
        else:
            headers[key] = value
    return SipMessage(start, headers, body, data, source)


def parse_auth_header(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not value:
        return result
    if value.lower().startswith("digest "):
        value = value[7:]
    for part in value.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        result[k.strip().lower()] = v.strip().strip('"')
    return result


def get_user_from_uri(uri: str) -> str:
    uri = uri.strip()
    if uri.startswith("<"):
        uri = uri[1 : uri.find(">")]
    if uri.lower().startswith("sip:"):
        uri = uri[4:]
    return uri.split("@", 1)[0].split(";", 1)[0].split(":", 1)[0]


def get_host_from_uri(uri: str) -> str:
    uri = uri.strip()
    if uri.startswith("<"):
        uri = uri[1 : uri.find(">")]
    if uri.lower().startswith("sip:"):
        uri = uri[4:]
    hostport = uri.split("@", 1)[-1].split(";", 1)[0]
    return hostport.split(":", 1)[0]


def extract_contact_endpoint(contact: str) -> Optional[tuple[str, int]]:
    if not contact:
        return None
    uri = contact
    if "<" in contact and ">" in contact:
        uri = contact[contact.find("<") + 1 : contact.find(">")]
    if uri.lower().startswith("sip:"):
        uri = uri[4:]
    hostport = uri.split("@", 1)[-1].split(";", 1)[0]
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        try:
            return host, int(port_s)
        except ValueError:
            return host, 5060
    return hostport, 5060


def make_tone_pcmu(
    seconds: float = 2.0,
    freq: float = 440.0,
    sample_rate: int = 8000,
    amplitude: float = 0.35,
) -> bytes:
    frames = int(seconds * sample_rate)
    samples = bytearray()
    for i in range(frames):
        value = int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        samples += struct.pack("<h", value)
    return linear16_to_pcmu(bytes(samples))


@dataclass
class Registration:
    username: str
    contact: str
    endpoint: tuple[str, int]
    expires: int
    updated_at: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return (time.time() - self.updated_at) < max(self.expires, 30)


@dataclass
class CallSession:
    direction: str  # "in" or "out"
    state: str  # "ringing" | "active"
    call_id: str
    from_header: str
    to_header: str
    cseq_num: int
    branch: str
    request_uri: str
    remote_sip: tuple[str, int]
    local_rtp: int
    remote_rtp: Optional[tuple[str, int]] = None
    invite_req: Optional[SipMessage] = None
    to_tag: str = ""
    remote_ext: str = ""


class SipPbx:
    def __init__(
        self,
        bind_ip: str,
        sip_port: int,
        accounts: dict[str, str],
        rtp_port: int,
        auto_answer: bool = True,
        auto_answer_destinations: Optional[set[str]] = None,
        auto_answer_delay: float = 0.0,
        play_tone_on_answer: bool = True,
        on_call_answered: Optional[Callable[[CallSession], None]] = None,
        on_call_ended: Optional[Callable[[], None]] = None,
    ):
        self.bind_ip = bind_ip
        # SIP/SDP must advertise a real IP (not 0.0.0.0). Updated toward phone on REGISTER.
        self.advertise_ip = config.resolve_advertise_ip(bind_ip)
        self.sip_port = sip_port
        self.accounts = accounts
        self.rtp_port = rtp_port
        self.auto_answer = auto_answer
        # If set, only these dialed extensions auto-answer (e.g. {"100"} for AI).
        self.auto_answer_destinations = auto_answer_destinations
        self.auto_answer_delay = max(0.0, float(auto_answer_delay))
        self.play_tone_on_answer = play_tone_on_answer
        self.on_call_answered = on_call_answered
        self.on_call_ended = on_call_ended
        self.realm = "telefon-local"
        self.registrations: dict[str, Registration] = {}
        self.call: Optional[CallSession] = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._stop = threading.Event()
        self._pending: dict[str, threading.Event] = {}
        self._responses: dict[str, SipMessage] = {}
        self._lock = threading.Lock()
        self._tone_stop = threading.Event()
        self._tone_thread: Optional[threading.Thread] = None
        self._on_event = None  # optional Callable[[str], None]

    def start(self) -> None:
        self.sock.bind((self.bind_ip, self.sip_port))
        threading.Thread(target=self._recv_loop, name="sip-recv", daemon=True).start()
        print(f"[pbx] listening on udp://{self.bind_ip}:{self.sip_port}", flush=True)
        if self.bind_ip in ("0.0.0.0", "::", "[::]"):
            print(f"[pbx] SIP/SDP advertise IP: {self.advertise_ip}", flush=True)
        print(f"[pbx] accounts: {', '.join(sorted(self.accounts))}", flush=True)
        print("[pbx] waiting for phone REGISTER...", flush=True)

    def stop(self) -> None:
        self._stop_tone()
        try:
            if self.call:
                self.hangup()
        except Exception:
            pass
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _emit(self, message: str) -> None:
        print(message, flush=True)
        if self._on_event:
            try:
                self._on_event(message)
            except Exception:
                pass

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                break
            if not data:
                continue
            try:
                msg = parse_sip(data, addr)
                self._dispatch(msg)
            except Exception as exc:  # noqa: BLE001 - keep server alive
                print(f"[pbx] parse/dispatch error from {addr}: {exc}")

    def _dispatch(self, msg: SipMessage) -> None:
        if msg.method:
            print(f"[sip] << {msg.method} from {msg.source[0]}:{msg.source[1]}")
            handler = {
                "REGISTER": self._handle_register,
                "OPTIONS": self._handle_options,
                "INVITE": self._handle_invite_in,
                "ACK": self._handle_ack,
                "BYE": self._handle_bye,
                "CANCEL": self._handle_cancel,
            }.get(msg.method)
            if handler:
                handler(msg)
            else:
                self._reply(msg, 405, "Method Not Allowed")
            return

        code = msg.status_code
        print(f"[sip] << {code} from {msg.source[0]}:{msg.source[1]}")
        cseq = msg.header("CSeq")
        call_id = msg.header("Call-ID")
        key = f"{call_id}|{cseq}"
        with self._lock:
            self._responses[key] = msg
            event = self._pending.get(key)
        if event:
            event.set()

    def _send(self, data: str, addr: tuple[str, int]) -> None:
        self.sock.sendto(data.encode("utf-8"), addr)

    def _reply(
        self,
        req: SipMessage,
        code: int,
        reason: str,
        extra_headers: Optional[list[str]] = None,
        body: str = "",
    ) -> None:
        to = req.header("To")
        if code >= 200 and "tag=" not in to.lower():
            to = f"{to};tag={uuid.uuid4().hex[:8]}"
        headers = [
            f"SIP/2.0 {code} {reason}",
            f"Via: {req.header('Via')}",
            f"From: {req.header('From')}",
            f"To: {to}",
            f"Call-ID: {req.header('Call-ID')}",
            f"CSeq: {req.header('CSeq')}",
            f"Contact: <sip:pbx@{self.advertise_ip}:{self.sip_port}>",
            f"User-Agent: telefon-pbx/1.0",
            "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS, REGISTER",
        ]
        if extra_headers:
            headers.extend(extra_headers)
        if body:
            headers.append(f"Content-Type: application/sdp")
            headers.append(f"Content-Length: {len(body.encode('utf-8'))}")
            packet = "\r\n".join(headers) + "\r\n\r\n" + body
        else:
            headers.append("Content-Length: 0")
            packet = "\r\n".join(headers) + "\r\n\r\n"
        self._send(packet, req.source)

    def _challenge(self, req: SipMessage) -> None:
        nonce = uuid.uuid4().hex
        auth = (
            f'Digest realm="{self.realm}", nonce="{nonce}", '
            'algorithm=MD5, qop="auth"'
        )
        self._reply(req, 401, "Unauthorized", [f"WWW-Authenticate: {auth}"])

    def _check_auth(self, req: SipMessage, username: str) -> bool:
        password = self.accounts.get(username)
        if password is None:
            return False
        auth_raw = req.header("Authorization")
        if not auth_raw:
            return False
        auth = parse_auth_header(auth_raw)
        if auth.get("username") != username:
            return False
        realm = auth.get("realm", self.realm)
        nonce = auth.get("nonce", "")
        uri = auth.get("uri", "")
        response = auth.get("response", "")
        ha1 = _md5(f"{username}:{realm}:{password}")
        ha2 = _md5(f"{req.method}:{uri}")
        qop = auth.get("qop")
        if qop == "auth":
            expected = _md5(
                f"{ha1}:{nonce}:{auth.get('nc', '')}:{auth.get('cnonce', '')}:{qop}:{ha2}"
            )
        else:
            expected = _md5(f"{ha1}:{nonce}:{ha2}")
        return response.lower() == expected.lower()

    def _handle_register(self, req: SipMessage) -> None:
        to_user = get_user_from_uri(req.header("To") or req.header("From"))
        if to_user not in self.accounts:
            self._reply(req, 403, "Forbidden")
            return
        if not self._check_auth(req, to_user):
            self._challenge(req)
            return

        expires_hdr = req.header("Expires")
        contact = req.header("Contact")
        expires = 3600
        if expires_hdr.isdigit():
            expires = int(expires_hdr)
        if "expires=" in contact.lower():
            for part in contact.split(";"):
                if part.strip().lower().startswith("expires="):
                    try:
                        expires = int(part.split("=", 1)[1])
                    except ValueError:
                        pass

        if expires == 0:
            self.registrations.pop(to_user, None)
            print(f"[pbx] unregistered {to_user}")
            self._reply(req, 200, "OK", [f"Contact: {contact}", "Expires: 0"])
            return

        endpoint = extract_contact_endpoint(contact)
        if not endpoint:
            # Some phones omit usable Contact host; fall back to packet source.
            endpoint = req.source
            contact = f"<sip:{to_user}@{endpoint[0]}:{endpoint[1]}>"

        self.registrations[to_user] = Registration(
            username=to_user,
            contact=contact if contact.startswith("<") else f"<{contact}>",
            endpoint=endpoint,
            expires=expires,
        )
        self._refresh_advertise_ip(endpoint[0])
        self._emit(f"[pbx] REGISTERED {to_user} -> {endpoint[0]}:{endpoint[1]} (expires={expires})")
        self._reply(
            req,
            200,
            "OK",
            [
                f"Contact: {self.registrations[to_user].contact};expires={expires}",
                f"Expires: {expires}",
            ],
        )

    def _handle_options(self, req: SipMessage) -> None:
        self._reply(req, 200, "OK", ["Accept: application/sdp"])

    def _handle_invite_in(self, req: SipMessage) -> None:
        if self.call and self.call.state in {"ringing", "active"}:
            self._reply(req, 486, "Busy Here")
            return

        request_uri = req.start_line.split()[1]
        dialed = get_user_from_uri(request_uri) or get_user_from_uri(req.header("To"))
        from_user = get_user_from_uri(req.header("From"))

        should_auto = self.auto_answer and (
            self.auto_answer_destinations is None or dialed in self.auto_answer_destinations
        )
        if not should_auto:
            self._emit(f"[call] no route for dialed {dialed} from {from_user}")
            self._reply(req, 404, "Not Found")
            return

        to_tag = uuid.uuid4().hex[:8]
        local_rtp = self.rtp_port + random.randint(0, 200)
        to_header = req.header("To")
        if "tag=" not in to_header.lower():
            to_header = f"{to_header};tag={to_tag}"

        self.call = CallSession(
            direction="in",
            state="ringing",
            call_id=req.header("Call-ID"),
            from_header=req.header("From"),
            to_header=to_header,
            cseq_num=int(req.header("CSeq").split()[0]),
            branch=req.via_branch(),
            request_uri=request_uri,
            remote_sip=req.source,
            local_rtp=local_rtp,
            remote_rtp=self._parse_sdp_endpoint(req.body),
            invite_req=req,
            to_tag=to_tag,
            remote_ext=from_user,
        )

        self._reply_call(req, 100, "Trying")
        self._reply_call(req, 180, "Ringing")
        self._emit(f"[call] INCOMING to {dialed} from {from_user}")

        if self.auto_answer:
            if self.auto_answer_delay > 0:
                self._emit(f"[call] ringing {self.auto_answer_delay:.1f}s then answer")
                call_id = self.call.call_id if self.call else ""

                def _delayed() -> None:
                    time.sleep(self.auto_answer_delay)
                    if self.call and self.call.state == "ringing" and self.call.call_id == call_id:
                        self.answer()

                threading.Thread(target=_delayed, name="auto-answer-delay", daemon=True).start()
            else:
                self.answer()

    def _reply_call(
        self,
        req: SipMessage,
        code: int,
        reason: str,
        body: str = "",
    ) -> None:
        """Reply using the current call's To-tag so the dialog stays consistent."""
        to = self.call.to_header if self.call else req.header("To")
        headers = [
            f"SIP/2.0 {code} {reason}",
            f"Via: {req.header('Via')}",
            f"From: {req.header('From')}",
            f"To: {to}",
            f"Call-ID: {req.header('Call-ID')}",
            f"CSeq: {req.header('CSeq')}",
            f"Contact: <sip:pbx@{self.advertise_ip}:{self.sip_port}>",
            "User-Agent: telefon-pbx/1.0",
            "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS, REGISTER",
        ]
        if body:
            headers.append("Content-Type: application/sdp")
            headers.append(f"Content-Length: {len(body.encode('utf-8'))}")
            packet = "\r\n".join(headers) + "\r\n\r\n" + body
        else:
            headers.append("Content-Length: 0")
            packet = "\r\n".join(headers) + "\r\n\r\n"
        self._send(packet, req.source)

    def _handle_ack(self, req: SipMessage) -> None:
        self._emit("[sip] ACK")

    def _handle_bye(self, req: SipMessage) -> None:
        self._reply(req, 200, "OK")
        self._stop_tone()
        self.call = None
        self._emit("[call] remote hung up")
        self._fire_call_ended()

    def _handle_cancel(self, req: SipMessage) -> None:
        if self.call and self.call.direction == "in" and self.call.state == "ringing":
            if self.call.invite_req:
                self._reply_call(self.call.invite_req, 487, "Request Terminated")
            self.call = None
            self._emit("[call] incoming cancelled")
            self._fire_call_ended()
        self._reply(req, 200, "OK")

    def _fire_call_answered(self, call: CallSession) -> None:
        if not self.on_call_answered:
            return

        def worker() -> None:
            try:
                self.on_call_answered(call)
            except Exception as exc:  # noqa: BLE001
                self._emit(f"[pbx] on_call_answered error: {exc}")

        threading.Thread(target=worker, name="call-answered", daemon=True).start()

    def _fire_call_ended(self) -> None:
        if not self.on_call_ended:
            return

        def worker() -> None:
            try:
                self.on_call_ended()
            except Exception as exc:  # noqa: BLE001
                self._emit(f"[pbx] on_call_ended error: {exc}")

        threading.Thread(target=worker, name="call-ended", daemon=True).start()

    def _local_sdp(self, rtp_port: Optional[int] = None) -> str:
        port = rtp_port or self.rtp_port
        return (
            "v=0\r\n"
            f"o=pbx {int(time.time())} {int(time.time())} IN IP4 {self.advertise_ip}\r\n"
            "s=telefon\r\n"
            f"c=IN IP4 {self.advertise_ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {port} RTP/AVP 0 8 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=fmtp:101 0-16\r\n"
            "a=sendrecv\r\n"
        )

    def _parse_sdp_endpoint(self, sdp: str) -> Optional[tuple[str, int]]:
        ip = None
        port = None
        for line in sdp.splitlines():
            line = line.strip()
            if line.startswith("c=IN IP4 "):
                ip = line.split()[-1]
            elif line.startswith("m=audio "):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    port = int(parts[1])
        if ip and port:
            return ip, port
        return None

    def _wait_response(self, call_id: str, cseq: str, timeout: float = 10.0) -> Optional[SipMessage]:
        key = f"{call_id}|{cseq}"
        event = threading.Event()
        with self._lock:
            self._pending[key] = event
        ok = event.wait(timeout)
        with self._lock:
            msg = self._responses.pop(key, None)
            self._pending.pop(key, None)
        return msg if ok else None

    def list_registrations(self) -> list[Registration]:
        return [r for r in self.registrations.values() if r.alive]

    def set_contact(
        self,
        extension: str,
        host: str,
        port: int = 5060,
        expires: int = 3600,
    ) -> Registration:
        """Manually point an extension at a phone IP (for direct dial without waiting)."""
        contact = f"<sip:{extension}@{host}:{port}>"
        reg = Registration(
            username=extension,
            contact=contact,
            endpoint=(host, port),
            expires=expires,
        )
        self.registrations[extension] = reg
        return reg

    def wait_for_register(self, extension: str, timeout: float = 120.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            if any(r.username == extension for r in self.list_registrations()):
                return True
            time.sleep(0.25)
        return False

    def call_status(self) -> str:
        if not self.call:
            return "idle"
        return f"{self.call.state}/{self.call.direction} peer={self.call.remote_ext or '?'}"

    def _refresh_advertise_ip(self, peer_ip: str) -> None:
        """Pick the NIC facing the phone so SDP/Contact RTP reaches us."""
        if config.SIP_ADVERTISE_IP:
            return
        if self.bind_ip not in ("0.0.0.0", "::", "[::]"):
            return
        new_ip = config.resolve_advertise_ip(self.bind_ip, peer_ip=peer_ip)
        if new_ip and new_ip != self.advertise_ip:
            self._emit(f"[pbx] SIP/SDP advertise IP: {self.advertise_ip} -> {new_ip} (toward {peer_ip})")
            self.advertise_ip = new_ip

    def answer(self) -> bool:
        call = self.call
        if not call or call.direction != "in" or call.state != "ringing" or not call.invite_req:
            self._emit("[call] nothing to answer")
            return False
        self._refresh_advertise_ip(call.remote_sip[0])
        sdp = self._local_sdp(call.local_rtp)
        self._reply_call(call.invite_req, 200, "OK", body=sdp)
        call.state = "active"
        if self.play_tone_on_answer and call.remote_rtp:
            self._start_tone(call.remote_rtp, bind_port=call.local_rtp, seconds=1.5)
        self._emit("[call] answered")
        self._fire_call_answered(call)
        return True

    def reject(self) -> bool:
        call = self.call
        if not call or call.direction != "in" or call.state != "ringing" or not call.invite_req:
            self._emit("[call] nothing to reject")
            return False
        self._reply_call(call.invite_req, 486, "Busy Here")
        self.call = None
        self._emit("[call] rejected")
        self._fire_call_ended()
        return True

    def hangup(self) -> bool:
        call = self.call
        if not call:
            self._emit("[call] no active call")
            return False

        self._stop_tone()

        if call.state == "ringing" and call.direction == "out":
            cancel = "\r\n".join(
                [
                    f"CANCEL {call.request_uri} SIP/2.0",
                    f"Via: SIP/2.0/UDP {self.advertise_ip}:{self.sip_port};branch={call.branch};rport",
                    "Max-Forwards: 70",
                    f"From: {call.from_header}",
                    f"To: {call.to_header}",
                    f"Call-ID: {call.call_id}",
                    f"CSeq: {call.cseq_num} CANCEL",
                    "Content-Length: 0",
                    "",
                    "",
                ]
            )
            self._send(cancel, call.remote_sip)
            self.call = None
            self._emit("[call] cancelled")
            self._fire_call_ended()
            return True

        if call.state == "ringing" and call.direction == "in":
            return self.reject()

        # Dialog local/remote swap depending on who sent the original INVITE.
        if call.direction == "in":
            bye_from, bye_to = call.to_header, call.from_header
            # Request-URI for BYE should be the remote contact; fall back to their IP.
            bye_uri = f"sip:{call.remote_ext}@{call.remote_sip[0]}:{call.remote_sip[1]}"
            bye_cseq = call.cseq_num + 1
        else:
            bye_from, bye_to = call.from_header, call.to_header
            bye_uri = call.request_uri
            bye_cseq = call.cseq_num + 1

        bye = "\r\n".join(
            [
                f"BYE {bye_uri} SIP/2.0",
                f"Via: SIP/2.0/UDP {self.advertise_ip}:{self.sip_port};branch=z9hG4bK{uuid.uuid4().hex};rport",
                "Max-Forwards: 70",
                f"From: {bye_from}",
                f"To: {bye_to}",
                f"Call-ID: {call.call_id}",
                f"CSeq: {bye_cseq} BYE",
                "Content-Length: 0",
                "",
                "",
            ]
        )
        self._send(bye, call.remote_sip)
        self.call = None
        self._emit("[call] hung up")
        self._fire_call_ended()
        return True

    def dial(
        self,
        extension: str,
        ring_seconds: float = 60.0,
        auto_hangup_after_tone: float = 0.0,
        *,
        connect_tone: bool = True,
    ) -> bool:
        """Place an outbound call. Keeps the call open unless auto_hangup_after_tone > 0."""
        if self.call:
            self._emit("[call] already in a call — hangup first")
            return False

        reg = self.registrations.get(extension)
        if not reg or not reg.alive:
            self._emit(f"[pbx] extension {extension} is not registered")
            return False

        call_id = uuid.uuid4().hex
        from_tag = uuid.uuid4().hex[:8]
        branch = f"z9hG4bK{uuid.uuid4().hex}"
        local_rtp = self.rtp_port + random.randint(0, 200)
        cseq_num = 1
        cseq = f"{cseq_num} INVITE"
        request_uri = f"sip:{extension}@{reg.endpoint[0]}:{reg.endpoint[1]}"
        caller_name = (getattr(config, "SIP_CALLER_ID", None) or "AiPhone").strip() or "AiPhone"
        caller_user = (getattr(config, "SIP_CALLER_USER", None) or "100").strip() or "100"
        from_header = (
            f"{_quote(caller_name)} <sip:{caller_user}@{self.advertise_ip}>;tag={from_tag}"
        )
        to_header = f"{_quote(extension)} <sip:{extension}@{self.advertise_ip}>"
        sdp = self._local_sdp(local_rtp)

        self.call = CallSession(
            direction="out",
            state="ringing",
            call_id=call_id,
            from_header=from_header,
            to_header=to_header,
            cseq_num=cseq_num,
            branch=branch,
            request_uri=request_uri,
            remote_sip=reg.endpoint,
            local_rtp=local_rtp,
            remote_ext=extension,
        )

        invite = "\r\n".join(
            [
                f"INVITE {request_uri} SIP/2.0",
                f"Via: SIP/2.0/UDP {self.advertise_ip}:{self.sip_port};branch={branch};rport",
                "Max-Forwards: 70",
                f"From: {from_header}",
                f"To: {to_header}",
                f"Call-ID: {call_id}",
                f"CSeq: {cseq}",
                f"Contact: <sip:{caller_user}@{self.advertise_ip}:{self.sip_port}>",
                "User-Agent: telefon-pbx/1.0",
                "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS, REGISTER",
                "Content-Type: application/sdp",
                f"Content-Length: {len(sdp.encode('utf-8'))}",
                "",
                sdp,
            ]
        )
        self._emit(f"[call] dialing {extension} at {reg.endpoint[0]}:{reg.endpoint[1]} ...")
        self._send(invite, reg.endpoint)

        deadline = time.time() + ring_seconds
        answered = None
        while time.time() < deadline and self.call and self.call.call_id == call_id:
            resp = self._wait_response(call_id, cseq, timeout=0.5)
            if not resp:
                continue
            code = resp.status_code or 0
            if code in {180, 183}:
                self._emit(f"[call] ringing ({code})")
                self.call.to_header = resp.header("To") or self.call.to_header
            elif code == 200:
                self.call.to_header = resp.header("To") or self.call.to_header
                answered = resp
                break
            elif code >= 300:
                self._emit(f"[call] failed: {code}")
                self.call = None
                return False

        if not answered or not self.call:
            if self.call and self.call.call_id == call_id:
                self.hangup()
            else:
                self._emit("[call] no answer")
            return False

        ack = "\r\n".join(
            [
                f"ACK {request_uri} SIP/2.0",
                f"Via: SIP/2.0/UDP {self.advertise_ip}:{self.sip_port};branch=z9hG4bK{uuid.uuid4().hex};rport",
                "Max-Forwards: 70",
                f"From: {from_header}",
                f"To: {self.call.to_header}",
                f"Call-ID: {call_id}",
                f"CSeq: {cseq_num} ACK",
                f"Contact: <sip:{caller_user}@{self.advertise_ip}:{self.sip_port}>",
                "Content-Length: 0",
                "",
                "",
            ]
        )
        self._send(ack, reg.endpoint)
        self.call.state = "active"
        self.call.remote_rtp = self._parse_sdp_endpoint(answered.body)
        self._emit("[call] connected — type 'hangup' to end, 'tone' to beep")

        if self.call.remote_rtp:
            if auto_hangup_after_tone > 0:
                self._play_tone_rtp(
                    self.call.remote_rtp,
                    bind_port=local_rtp,
                    seconds=auto_hangup_after_tone,
                )
                self.hangup()
            elif connect_tone:
                self._start_tone(self.call.remote_rtp, bind_port=local_rtp, seconds=1.5)
        return True

    def play_tone(self, seconds: float = 2.0) -> bool:
        call = self.call
        if not call or call.state != "active" or not call.remote_rtp:
            self._emit("[call] no active audio session")
            return False
        self._start_tone(call.remote_rtp, bind_port=call.local_rtp, seconds=seconds)
        return True

    def _start_tone(
        self,
        remote: tuple[str, int],
        bind_port: Optional[int] = None,
        seconds: float = 0.0,
    ) -> None:
        """Play tone; seconds<=0 means until stopped."""
        self._stop_tone()
        self._tone_stop.clear()

        def worker() -> None:
            payload = make_tone_pcmu(seconds=max(seconds, 0.2) if seconds > 0 else 1.0, freq=440.0)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                rtp_ip = (
                    self.advertise_ip
                    if self.bind_ip in ("0.0.0.0", "::", "[::]")
                    else self.bind_ip
                )
                sock.bind((rtp_ip, bind_port or 0))
                ssrc = random.randint(1, 0xFFFFFFFF)
                seq = random.randint(0, 0xFFFF)
                timestamp = random.randint(0, 0xFFFFFFFF)
                frame = 160
                end = time.time() + seconds if seconds > 0 else None
                while not self._tone_stop.is_set():
                    for offset in range(0, len(payload), frame):
                        if self._tone_stop.is_set():
                            break
                        if end and time.time() >= end:
                            return
                        chunk = payload[offset : offset + frame]
                        if not chunk:
                            break
                        header = struct.pack(
                            "!BBHII",
                            0x80,
                            0x00,
                            seq & 0xFFFF,
                            timestamp & 0xFFFFFFFF,
                            ssrc,
                        )
                        sock.sendto(header + chunk, remote)
                        seq += 1
                        timestamp += len(chunk)
                        time.sleep(0.02)
                    if seconds > 0:
                        return
            finally:
                sock.close()

        self._tone_thread = threading.Thread(target=worker, name="rtp-tone", daemon=True)
        self._tone_thread.start()

    def _stop_tone(self) -> None:
        self._tone_stop.set()
        t = self._tone_thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._tone_thread = None

    def call_extension(self, extension: str, ring_seconds: float = 25.0, tone_seconds: float = 3.0) -> bool:
        """One-shot call: ring, play tone, hang up (used by call.py / ring.py)."""
        return self.dial(
            extension,
            ring_seconds=ring_seconds,
            auto_hangup_after_tone=tone_seconds,
        )

    def _play_tone_rtp(
        self,
        remote: tuple[str, int],
        bind_port: Optional[int] = None,
        seconds: float = 3.0,
    ) -> None:
        payload = make_tone_pcmu(seconds=seconds, freq=440.0)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rtp_ip = (
                self.advertise_ip
                if self.bind_ip in ("0.0.0.0", "::", "[::]")
                else self.bind_ip
            )
            sock.bind((rtp_ip, bind_port or 0))
            ssrc = random.randint(1, 0xFFFFFFFF)
            seq = random.randint(0, 0xFFFF)
            timestamp = random.randint(0, 0xFFFFFFFF)
            frame = 160  # 20ms @ 8kHz
            for offset in range(0, len(payload), frame):
                chunk = payload[offset : offset + frame]
                if not chunk:
                    break
                header = struct.pack(
                    "!BBHII",
                    0x80,  # V=2, P=0, X=0, CC=0
                    0x00,  # M=0, PT=0 (PCMU)
                    seq & 0xFFFF,
                    timestamp & 0xFFFFFFFF,
                    ssrc,
                )
                sock.sendto(header + chunk, remote)
                seq += 1
                timestamp += len(chunk)
                time.sleep(0.02)
        finally:
            sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local SIP PBX for an IP phone")
    parser.add_argument("--bind", default=config.BIND_IP)
    parser.add_argument("--port", type=int, default=config.SIP_PORT)
    parser.add_argument("--ext", default=config.PHONE_EXTENSION, help="extension to ring")
    args = parser.parse_args()

    pbx = SipPbx(args.bind, args.port, config.ACCOUNTS, config.RTP_PORT)
    pbx.start()
    print()
    print("Phone SIP settings:")
    print(f"  SIP server / registrar : {args.bind}")
    print(f"  SIP port               : {args.port}")
    print(f"  SIP user / auth user   : {config.PHONE_EXTENSION}")
    print(f"  SIP password           : {config.ACCOUNTS[config.PHONE_EXTENSION]}")
    print(f"  Transport              : UDP")
    print()
    if not sys.stdin.isatty():
        print("Non-interactive mode: waiting for REGISTER, then ringing once...")
        try:
            deadline = time.time() + 180
            while time.time() < deadline:
                if any(r.username == args.ext for r in pbx.list_registrations()):
                    pbx.call_extension(args.ext)
                    break
                time.sleep(0.5)
            else:
                print("Timed out waiting for REGISTER.")
            # Keep running so the phone can re-REGISTER / you can Ctrl+C
            print("PBX still running. Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print()
        finally:
            pbx.stop()
        return

    print("Commands: Enter = ring phone | r = show registrations | q = quit")
    try:
        while True:
            cmd = input("> ").strip().lower()
            if cmd in {"q", "quit", "exit"}:
                break
            if cmd in {"r", "reg", "regs"}:
                regs = pbx.list_registrations()
                if not regs:
                    print("  (none)")
                for reg in regs:
                    print(f"  {reg.username} -> {reg.endpoint[0]}:{reg.endpoint[1]}")
                continue
            pbx.call_extension(args.ext)
    except KeyboardInterrupt:
        print()
    finally:
        pbx.stop()


if __name__ == "__main__":
    main()
