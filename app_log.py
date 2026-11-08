"""Tee stdout/stderr to logs/aiphone.log (for windowless / Neutralino shell)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, TextIO

from app_paths import app_dir

_LOG_NAME = "aiphone.log"
_tee: Optional["Tee"] = None


def log_dir() -> Path:
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return log_dir() / _LOG_NAME


class Tee:
    """Write to original stream (if any) and a log file."""

    def __init__(self, stream: Optional[TextIO], path: Path) -> None:
        self._stream = stream
        self._path = path
        self._file = path.open("a", encoding="utf-8", errors="replace", buffering=1)

    def write(self, data: str) -> int:
        if self._stream is not None:
            try:
                self._stream.write(data)
                self._stream.flush()
            except Exception:
                pass
        try:
            self._file.write(data)
            self._file.flush()
        except Exception:
            pass
        return len(data) if isinstance(data, str) else 0

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass
        try:
            self._file.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        if self._stream is None:
            return False
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        if self._stream is None:
            raise OSError("no underlying stream")
        return self._stream.fileno()

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


def setup_file_logging() -> Path:
    """Install stdout/stderr tees. Idempotent. Returns log path."""
    global _tee
    path = log_path()
    if _tee is not None:
        return path
    # Windowless builds often have stdout/stderr = None — log file still works.
    base_out = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
    base_err = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
    out = Tee(base_out, path)
    err = Tee(base_err, path)
    sys.stdout = out  # type: ignore[assignment]
    sys.stderr = err  # type: ignore[assignment]
    _tee = out
    print(f"[log] writing to {path}", flush=True)
    return path


def clear_log() -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
