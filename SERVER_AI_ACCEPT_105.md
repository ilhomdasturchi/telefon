# Prompt for the AI on the server — accept test call from 105

Copy everything below the line into the server AI chat.

---

## Task

Prepare this Windows server so **AiPhone accepts a diagnostic call from extension 105**, and confirm whether mic RTP is received.

Do not change application code unless required. Prefer restart + firewall + `.env` only.

## What will happen from the LAN

Another machine (`192.168.1.100`) will:

1. **REGISTER** as SIP user **`105`** / password **`105`** to `192.168.1.200:5060`
2. **INVITE** **`100`** (the AI extension)
3. Send **PCMU RTP** (fake “voice”) for ~8–10 seconds
4. Hang up with **BYE**

You must have AiPhone running and **idle** (not already in a call), or the caller gets `486 Busy Here`.

## Required state on this server

1. Start / restart AiPhone (`AiPhone.exe` or equivalent) so UDP **5060** is listening.
2. `.env` next to the exe must include:

```env
BIND_IP=0.0.0.0
SIP_ADVERTISE_IP=192.168.1.200
GEMINI_API_KEY=...
```

3. Windows Firewall inbound allow (if not already):

```powershell
New-NetFirewallRule -DisplayName "AiPhone SIP 5060" -Direction Inbound -Protocol UDP -LocalPort 5060 -Action Allow -Profile Any -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "AiPhone RTP 10000-10200" -Direction Inbound -Protocol UDP -LocalPort 10000-10200 -Action Allow -Profile Any -ErrorAction SilentlyContinue
```

4. No active call on ext 100 (hang up / restart AiPhone if busy).

5. Confirm listen:

```powershell
netstat -ano | findstr ":5060"
```

## During the test call — watch the console

Success looks like:

```
[pbx] REGISTERED 105 -> 192.168.1.100:...
[call] INCOMING to 100 from 105
[call] answered
[rtp] bridge 192.168.1.200:xxxxx <-> 192.168.1.100:xxxxx
[rtp] rx #1 from 192.168.1.100:...
```

Failure looks like:

```
[rtp] tx #... to 192.168.1.100:...
[ai] WARNING: no UDP at all ...
```

(or no `[rtp] rx #1` at all)

## Notes

- Ext **105** is a normal account (password = extension). No special whitelist is required — any registered user may dial **100**.
- RDP is unrelated.
- After the test, reply with: whether AiPhone was running, whether `REGISTERED 105` appeared, and whether `[rtp] rx #1` appeared.

## When ready

Reply exactly: `READY FOR 105` so the other side can place the call.
