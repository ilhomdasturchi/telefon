# Telefon Neutralino shell

Desktop UI for AiPhone (status, IP settings, logs, autostart, hash auto-update).

## Dev

```powershell
# From repo root after binaries exist in desktop/bin
cd desktop
neu run
```

Fetch binaries if missing (Windows):

```powershell
# binaries already documented in build_exe.ps1
```

## Portable layout (next to telefon.exe)

- `telefon.exe` — this shell
- `resources.neu` — bundled UI
- `AiPhone.exe` — windowless SIP backend
- `.env`
- `logs/aiphone.log`
