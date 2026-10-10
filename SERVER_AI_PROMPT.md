# Prompt for the AI on the server (192.168.1.200)

Copy everything below the line into the server AI chat.

---

## Task

Diagnose and fix one-way audio on the AiPhone SIP/Gemini app on **this Windows server**.

Symptom: the desk phone **hears** the AI (greetings / TTS), but the AI **does not hear** the user. Console shows RTP **tx** to the phone and **no RTP rx** from the phone.

Do **not** blame RDP. RDP is only used to control the desktop; SIP/RTP is separate LAN UDP.

## Topology

| Role | IP |
|------|-----|
| This server (AiPhone / SIP PBX) | **192.168.1.200** |
| Desk phone (Panasonic KX-HDV130) | **192.168.1.190** |
| Dev PC (same LAN, used for remote probe) | **192.168.1.100** |

App: `AiPhone.exe` (or `python ai_phone.py`) with `.env` next to it. Expected `.env` keys:

```env
GEMINI_API_KEY=...
GEMINI_LIVE_MODEL=gemini-3.8-live
BIND_IP=0.0.0.0
SIP_ADVERTISE_IP=192.168.1.200
```

SIP UDP **5060**. RTP UDP roughly **10000–10200** (random per call).

## Already verified FROM the LAN (dev PC 192.168.1.100 → this server)

Run date: probe from client. Results:

1. **Ping 192.168.1.200** — OK, 0% loss, &lt;1 ms, TTL=64  
2. **Same L2 segment** — traceroute one hop; ARP `192.168.1.200` → `f0-76-1c-83-b4-b6`  
3. **SIP UDP 5060** — AiPhone answered:

```
SIP/2.0 200 OK
Contact: <sip:pbx@192.168.1.200:5060>
User-Agent: telefon-pbx/1.0
```

So: host is up, AiPhone is listening, advertise IP in Contact is already **192.168.1.200** (correct).  
LAN path for SIP works. The failure is almost certainly **inbound UDP RTP on this server** (firewall / security product), or the phone not sending audio to the SDP IP:port.

Client-side “UDP send to 10000/10059/…” succeeding does **not** prove this server accepts those packets.

## Typical bad call log (from this server)

```
[rtp] tx #50 to 192.168.1.190:16298
[ai] phone should send RTP to 192.168.1.200:10059 ...
[ai] WARNING: no RTP from phone yet — AI cannot hear you.
```

Meaning: server → phone RTP works; phone → server RTP never arrives (or is dropped before the process).

## What you must do ON THIS SERVER

### 1) Confirm process and listen ports

```powershell
Get-Process AiPhone, python -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, Path
netstat -ano | findstr ":5060"
netstat -ano | findstr ":100"
```

Expect UDP bind on `0.0.0.0:5060` or `192.168.1.200:5060`, and during a call a UDP port in 10000–10200 owned by AiPhone/python.

### 2) Fix Windows Firewall (most likely root cause)

Outbound often allowed (phone hears AI); inbound RTP blocked (AI deaf).

Run **elevated** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "AiPhone SIP 5060" -Direction Inbound -Protocol UDP -LocalPort 5060 -Action Allow -Profile Any -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "AiPhone RTP 10000-10200" -Direction Inbound -Protocol UDP -LocalPort 10000-10200 -Action Allow -Profile Any -ErrorAction SilentlyContinue

Get-NetFirewallRule -DisplayName "AiPhone*" | Format-Table DisplayName, Enabled, Direction, Action
Get-NetFirewallPortFilter | Where-Object { $_.LocalPort -eq 5060 -or $_.LocalPort -eq '10000-10200' }
```

Also check third-party antivirus / “network protection” that might drop UDP.

### 3) Confirm .env advertise IP

In the folder with `AiPhone.exe`:

```powershell
Get-Content .env
```

Must include:

```env
SIP_ADVERTISE_IP=192.168.1.200
BIND_IP=0.0.0.0
```

Restart AiPhone after editing `.env`.

### 4) Live packet check during one test call

While a call to ext 100 is active and user speaks into the handset:

```powershell
# Replace PORT with the RTP port printed in the AiPhone console, e.g. 10059
pktmon filter add -t udp -p PORT
pktmon start --etw -f C:\Temp\rtp.etl
# speak for ~10 seconds, then:
pktmon stop
pktmon etl2txt C:\Temp\rtp.etl -o C:\Temp\rtp.txt
Select-String -Path C:\Temp\rtp.txt -Pattern "192.168.1.190" | Select-Object -First 20
```

Or Wireshark/tshark filter: `udp && ip.addr==192.168.1.190 && udp.port==PORT`

Interpretation:

| Observation | Meaning |
|-------------|---------|
| No UDP from `.190` to server RTP port | Phone not sending, or switch/firewall before Windows, or wrong SDP IP on phone |
| UDP arrives in pktmon but AiPhone still “no RTP” | Process bind/codec issue (rare if SIP works) |
| No UDP in pktmon + firewall was blocking | Open firewall (step 2), retest |

### 5) After a fixed call, AiPhone console should show

```
[rtp] rx #1 from 192.168.1.190:...
```

If you see `got N UDP but no PCMU/PCMA` → set phone audio codec to **G.711 μ-law (PCMU)** or **A-law (PCMA)**.

### 6) Optional: temporarily disable firewall profile to confirm

**Only for a short test**, elevated:

```powershell
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False
# one test call, then RE-ENABLE:
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
```

If hearing works only with firewall off → rules in step 2 are the fix.

## Success criteria

1. Firewall rules for UDP 5060 and 10000–10200 inbound exist and are Allow.  
2. `.env` has `SIP_ADVERTISE_IP=192.168.1.200`.  
3. Test call shows `[rtp] rx #1 from 192.168.1.190:...` and AI responds to speech.  
4. Report back: what you changed, firewall rule status, and whether `rx #1` appeared.

## Out of scope / already ruled out from client probe

- Server offline — ruled out (ping OK)  
- AiPhone not running / SIP dead — ruled out (OPTIONS → 200 OK, Contact `@192.168.1.200`)  
- Wrong advertise IP in Contact — ruled out (already 192.168.1.200)  
- RDP causing one-way audio — ruled out (unrelated path)
