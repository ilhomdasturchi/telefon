"""Local SIP PBX settings for the IP phone."""

import os
import socket

from app_paths import resource_dir

_RESOURCE_DIR = str(resource_dir())

# This PC (SIP server the phone registers to).
# BIND_IP=0.0.0.0 listens on all interfaces; set a specific IP to bind only that one.
BIND_IP = (os.environ.get("BIND_IP") or "0.0.0.0").strip() or "0.0.0.0"
# IP written into SIP Contact / SDP. Required when BIND_IP=0.0.0.0 (auto-detected if empty).
SIP_ADVERTISE_IP = (os.environ.get("SIP_ADVERTISE_IP") or "").strip()
SIP_PORT = int((os.environ.get("SIP_PORT") or "5060").strip() or "5060")


def local_ip_toward(peer_ip: str) -> str | None:
    """Local IPv4 used to reach peer_ip (picks the right NIC on multi-homed hosts)."""
    peer_ip = (peer_ip or "").strip()
    if not peer_ip or peer_ip in ("0.0.0.0", "127.0.0.1"):
        return None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((peer_ip, 9))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("0."):
                return ip
        finally:
            sock.close()
    except OSError:
        return None
    return None


def resolve_advertise_ip(bind_ip: str | None = None, peer_ip: str | None = None) -> str:
    """Real IPv4 for SIP/SDP (never 0.0.0.0). Prefer peer-directed NIC, then bind, then default route."""
    if SIP_ADVERTISE_IP:
        return SIP_ADVERTISE_IP
    bind = (bind_ip if bind_ip is not None else BIND_IP).strip() or "0.0.0.0"
    if bind not in ("0.0.0.0", "::", "[::]"):
        return bind
    toward = local_ip_toward(peer_ip or "")
    if toward:
        return toward
    toward = local_ip_toward("8.8.8.8")
    if toward:
        return toward
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"

# RTP port range used when this PBX places a call
RTP_PORT = 10000

# Local extensions 100–999 (password = extension number)
ACCOUNTS = {str(n): str(n) for n in range(100, 1000)}

# Special numbers
AI_EXTENSION = "100"  # dial this to talk to Gemini
PHONE_EXTENSION = "101"  # desk phone (Panasonic)
PHONE_IP = "192.168.1.190"

# Gemini Live (non-secret default; key comes from .env)
GEMINI_LIVE_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live")
# Google Search grounding (separate free-tier quota — causes Live 1011 when exhausted)
GEMINI_LIVE_GOOGLE_SEARCH = os.environ.get("GEMINI_LIVE_GOOGLE_SEARCH", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Ring before AI answers (phone plays local ringback on 180 Ringing)
AI_RING_SECONDS = 1.5
# Phone mic → Gemini (narrowband RTP is quiet; slight boost helps ASR)
MIC_GAIN = float((os.environ.get("MIC_GAIN") or "2.2").strip() or "2.2")
GEMINI_SYSTEM_INSTRUCTION = (
    "This AI name is Alomat, 22 years old girl lives in internet"
    "If asks about creator (even of this AI) Say 'Bizni Allah yaratgan'"
    "If asks about developer say 'Mani Ilhom aka dasturchi yasaganlar'"
    "Everytime speak Uzbek language. "
    "Foydalanuvchi o'zbekcha gapiradi; telefon audiosi noaniq bo'lsa ham tushunishga harakat qil. "
    "Even if user input doesn't sound like Uzbek, try to understand and respond in Uzbek. "
    "Answer short and sweet. "
    "Chaqiriq boshida salomlashmang — foydalanuvchi allaqachon prerecorded salomni eshitgan. "
    "Answer ASAP, but if you need to think, say 'Hozir, bilib kelaman'. "
    "For dollar/kurs/valyuta use get_exchange_rate tool (Markaziy bank). "
    "For weather/ob-havo use get_weather tool. "
    "Do not invent live numbers. For other internet facts you cannot look up, say so briefly. "
    "Silence / idle prompts are sent by the phone system as text instructions — follow them exactly when you receive them. "
    # Farewell phrase is chosen in code per call and appended (see pick_random_farewell).
)

# Pre-recorded call phrases (WAV under PHRASE_DIR). One of each category per call.
# Generate: python generate_phrases.py --force
PHRASE_DIR = os.path.join(_RESOURCE_DIR, "assets", "phrases")
_PHRASE_CALM = (
    "Calm natural adult Uzbek phone voice. Neutral, understated. "
    "NOT cheerful, NOT excited. Like a polite receptionist."
)
FAREWELL_PHRASES = (
    {
        "id": "xayr",
        "speak": "Xayr!",
        "style": f"{_PHRASE_CALM} Brief goodbye.",
    },
    {
        "id": "sog_boling",
        "speak": "Sog' bo'ling!",
        "style": f"{_PHRASE_CALM} Warm but calm goodbye.",
    },
    {
        "id": "bopti_charchamang",
        "speak": "Bo'pti, charchamang!",
        "style": f"{_PHRASE_CALM} Casual polite closing.",
    },
)
HELP_OFFER_PHRASES = (
    {
        "id": "shetdamisiz",
        "speak": "Shetdamisiz?",
        "style": f"{_PHRASE_CALM} Mild question — checking if still there.",
    },
    {
        "id": "ketib_qoldizmi",
        "speak": "Ketib qoldizmi?",
        "style": f"{_PHRASE_CALM} Mild question — did you leave?",
    },
    {
        "id": "uxlab_qoldizmi",
        "speak": "Uxlab qoldizmi?",
        "style": f"{_PHRASE_CALM} Mild playful question — fell asleep?",
    },
    {
        "id": "aloqadamizmi",
        "speak": "Aloqadamizmi?",
        "style": f"{_PHRASE_CALM} Mild question — are we still connected?",
    },
)
IDLE_FAREWELL_PHRASES = (
    {
        "id": "ketishim_kerak",
        "speak": "Hozir ketishim kerak, yordam kerak bo'lsa yana telefon qilarsiz.",
        "style": f"{_PHRASE_CALM} Polite leave-taking.",
    },
    {
        "id": "xayr_telefon",
        "speak": "Xayr, kerak bo'lsa yana telefon qiling.",
        "style": f"{_PHRASE_CALM} Short goodbye with callback invite.",
    },
    {
        "id": "bopti_telefon",
        "speak": "Bo'pti, charchamang. Yordam kerak bo'lsa yana telefon qiling.",
        "style": f"{_PHRASE_CALM} Casual goodbye with callback invite.",
    },
)
# Phrase TTS: try this Gemini model first; generate_phrases.py falls back on 429
# → 3.1 / 2.5 / 3.8-lite → edge → local mms
PHRASE_TTS_MODEL = os.environ.get("PHRASE_TTS_MODEL", "gemini-3.1-flash-tts-preview")

PHRASE_TTS_VOICE = os.environ.get("PHRASE_TTS_VOICE", "Kore")

# Mutual silence (neither user nor AI speaking). Remind, then farewell+hang.
# Gaps while the other person is talking do NOT count — clock resets on any speech.
SILENCE_OFFER_HELP_SECONDS = float(os.environ.get("SILENCE_OFFER_HELP_SECONDS", "10"))
SILENCE_HANGUP_SECONDS = float(os.environ.get("SILENCE_HANGUP_SECONDS", "10"))

# Detected in AI output transcription if the model forgets to call hang_up
HANGUP_PHRASES = (
    "xayr",
    "salomat bo‘ling",
     "sog‘ bo‘ling",
    "bo'pti, charchamen",
    "telefon qib turing",
)

# Instant pre-recorded greetings — one played at random on answer
# Calm, natural phone tone — not overly cheerful
_CALM = (
    "Calm natural adult phone voice. Neutral-positive, understated. "
    "NOT cheerful, NOT excited, NOT smiling hard, NOT cartoonish. "
    "Like a polite receptionist answering a call."
)

GREETING_DIR = os.path.join(_RESOURCE_DIR, "assets", "greetings")
GREETINGS = [
    {
        "id": "labbay",
        "label": "Labbay",
        "speak": "Labbe",
        "style": (
            f"{_CALM} "
            "Pronounce exactly as two syllables Lab-be (Labbe), not Lab-bay. "
            "Steady, ready, brief."
        ),
        "lock": False,
    },
    {
        "id": "eshitaman",
        "label": "Eshitaman",
        "speak": "Eshitaman?",
        "style": (
            f"{_CALM} "
            "Mild rising question intonation ('I'm listening?'). "
            "Soft and natural, not bright or bubbly."
        ),
        "lock": False,
    },
    {
        "id": "allo",
        "label": "Allo",
        "speak": "Alyo",
        "style": (
            f"{_CALM} "
            "Pronounce as al-yo (Alyo), NOT English 'allo' or 'hello'. "
            "Simple phone pickup, quiet and natural."
        ),
        "lock": False,
    },
    {
        "id": "yaxshimisiz",
        "label": "Yaxshimisiz",
        "speak": "Yaxshimisiz?",
        "style": (
            f"{_CALM} "
            "Mild rising question intonation ('How are you?'). "
            "Polite, soft, not energetic."
        ),
        "lock": False,
    },
    {
        "id": "assalomu_aleykum",
        "label": "Assalomu aleykum",
        "speak": "Assalomu aleykum",
        "style": (
            f"{_CALM} "
            "Standard Uzbek greeting. Even, respectful, not joyful."
        ),
        "lock": False,
    },
    {
        "id": "nima_xizmat",
        "label": "Nima xizmat?",
        "speak": "Nima xizmat?",
        "style": (
            f"{_CALM} "
            "Mild rising question intonation ('How can I help?'). "
            "Matter-of-fact and polite."
        ),
        "lock": False,
    },
]
# Greeting TTS: "mms" (local Uzbek, natural), "edge", or "gemini" (quota-limited)
GREETING_TTS_ENGINE = os.environ.get("GREETING_TTS_ENGINE", "mms")
MMS_TTS_MODEL = os.environ.get(
    "MMS_TTS_MODEL",
    "MuzaffarSharofitdinov/mms-tts-uzbek-girl-voice_cyrillic",
)
MMS_LENGTH_SCALE = float(os.environ.get("MMS_LENGTH_SCALE", "1.15"))  # >1 = slower
MMS_NOISE_SCALE = float(os.environ.get("MMS_NOISE_SCALE", "0.333"))
EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")  # or uz-UZ-SardorNeural
EDGE_TTS_RATE = os.environ.get("EDGE_TTS_RATE", "-5%")   # slightly slower = calmer
EDGE_TTS_PITCH = os.environ.get("EDGE_TTS_PITCH", "-2Hz")
GREETING_TTS_MODEL = os.environ.get("GREETING_TTS_MODEL", "gemini-3.8-flash-tts")
GREETING_VOICE = os.environ.get("GREETING_VOICE", "Kore")
