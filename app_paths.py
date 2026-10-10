"""Resolve app / resource dirs for source runs and frozen .exe builds."""

from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Writable/config dir: next to the .exe when frozen (for .env)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Bundled assets root (onefile temp extract, or project / beside-exe override)."""
    if getattr(sys, "frozen", False):
        beside = app_dir() / "assets"
        if beside.is_dir():
            return app_dir()
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return app_dir()
    return Path(__file__).resolve().parent


def env_path() -> Path:
    return app_dir() / ".env"
