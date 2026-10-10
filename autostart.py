"""Register / unregister Windows login auto-start for the frozen .exe."""

from __future__ import annotations

import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AiPhone"


def _exe_command() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    return f'"{Path(sys.executable).resolve()}"'


def ensure_autostart() -> bool:
    """Add HKCU Run entry so the .exe starts at login. No-op when not frozen."""
    cmd = _exe_command()
    if not cmd:
        return False
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
    print(f"[autostart] Registered Windows login start: {_VALUE_NAME}", flush=True)
    return True


def remove_autostart() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        print(f"[autostart] Removed Windows login start: {_VALUE_NAME}", flush=True)
        return True
    except FileNotFoundError:
        return False
