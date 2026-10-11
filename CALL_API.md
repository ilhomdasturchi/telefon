# Telefon call API

AiPhone exposes a small HTTP endpoint so other software can make the desk phone **ring** or **ring and speak a message**.

Requires `python ai_phone.py` (or `AiPhone.exe`) running, and the phone registered.

---

## Enable

In `.env` (same folder as the app):

```env
CALL_API_KEY=choose-a-long-secret
CALL_API_HOST=0.0.0.0
CALL_API_PORT=8088
# Short ring duration when type=ring (seconds)
CALL_API_RING_SEC=2
```

Restart AiPhone. Log line:

```text
[call-api] listening http://0.0.0.0:8088/api/call (types: announce | ring)
```

Open firewall UDP (SIP) as before; for this API open **TCP 8088** (or your `CALL_API_PORT`) from senders that need access.

---

## Auth

Send the key in any one of:

| Place | Example |
|--------|---------|
| Header | `X-Call-Key: YOUR_KEY` |
| Header | `Authorization: Bearer YOUR_KEY` |
| JSON body | `"key": "YOUR_KEY"` |

---

## Types (sender chooses)

| `type` | Behavior |
|--------|----------|
| `announce` | Dial the phone, on answer play TTS message once → prerecorded **qaytaraman** → same message again, hang up |
| `ring` | Dial and ring only for ~1–2 seconds, then hang up (no answer / no TTS needed) |

Aliases accepted:

- announce: `message`, `speak`, `play`, `tts`
- ring: `ping`, `short`, `buzz`

---

## Endpoints

### `GET /api/health`

No auth. Quick status.

```bash
curl http://192.168.1.100:8088/api/health
```

Example:

```json
{
  "ok": true,
  "service": "telefon-call-api",
  "extension": "101",
  "registered": true,
  "busy": false,
  "types": ["announce", "ring"]
}
```

### `POST /api/call`

JSON body.

#### Announce (call + play message)

```bash
curl -X POST http://192.168.1.100:8088/api/call \
  -H "Content-Type: application/json" \
  -H "X-Call-Key: YOUR_KEY" \
  -d "{\"type\":\"announce\",\"message\":\"Emas oson bu maydon ichra turmoq.\"}"
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"announce"` |
| `message` | yes | Text to speak (also accepts `speak` / `text`) |
| `extension` | no | Default desk phone from app (`101`) |
| `key` | if no header | API key |

Success: `{"ok":true,"type":"announce",...}`  
Use a **long client timeout** (announce can take 1–2 minutes).

#### Ring only (1–2 seconds)

```bash
curl -X POST http://192.168.1.100:8088/api/call \
  -H "Content-Type: application/json" \
  -H "X-Call-Key: YOUR_KEY" \
  -d "{\"type\":\"ring\",\"ringSeconds\":2}"
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"ring"` |
| `ringSeconds` | no | Default `CALL_API_RING_SEC` (2). Clamped 0.5–10 |
| `extension` | no | Default `101` |

Success: `{"ok":true,"type":"ring","ringSeconds":2,...}`  
Phone need not answer; it just rings briefly.

---

## Errors

| HTTP | `error` | Meaning |
|------|---------|---------|
| 401 | `unauthorized` | Bad/missing key |
| 400 | `bad_type` / `invalid_json` / `missing_message` | Bad request |
| 409 | `busy` / `not_registered` / `announce_unsuccessful` | Phone busy, not registered, or no answer on announce |
| 404 | `not_found` | Wrong path |

---

## Notes

- Only **one** outbound call at a time (Vazifa critical + this API share the phone).
- `announce` uses the same TTS settings as critical alerts (Edge primary, slower rate, lead silence).
- Replace `192.168.1.100` with this PC’s LAN IP (`SIP_ADVERTISE_IP`).
