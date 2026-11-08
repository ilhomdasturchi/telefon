"""Upload dist update artifacts to marvar.id SFTP /telefon/."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    import paramiko
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install paramiko: python -m pip install paramiko") from exc

ROOT = Path(__file__).resolve().parents[1]


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = [p for p in remote_dir.split("/") if p]
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)
            print(f"mkdir {cur}", flush=True)


def main() -> int:
    load_dotenv(ROOT / ".env", override=True)
    host = (os.environ.get("TELEFON_SFTP_HOST") or "").strip()
    port = int((os.environ.get("TELEFON_SFTP_PORT") or "2022").strip() or "2022")
    user = (os.environ.get("TELEFON_SFTP_USER") or "").strip()
    password = os.environ.get("TELEFON_SFTP_PASSWORD") or ""
    remote_dir = (os.environ.get("TELEFON_SFTP_REMOTE_DIR") or "/telefon").strip().rstrip("/") or "/telefon"
    base = (os.environ.get("TELEFON_UPDATE_BASE_URL") or "https://marvar.id/media/telefon").strip().rstrip("/")

    if not host or not user or not password:
        print("Missing TELEFON_SFTP_HOST / TELEFON_SFTP_USER / TELEFON_SFTP_PASSWORD in .env", file=sys.stderr)
        return 1

    files = ["update.json", "telefon.zip", "telefon.exe"]
    locals_ = []
    for name in files:
        path = ROOT / "dist" / name
        if not path.is_file():
            print(f"Missing {path} - run npm run build first", file=sys.stderr)
            return 1
        locals_.append((path, f"{remote_dir}/{name}"))

    transport = paramiko.Transport((host, port))
    try:
        transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        assert sftp is not None
        try:
            _ensure_remote_dir(sftp, remote_dir)
            for local, remote in locals_:
                print(f"put {local.name} -> {remote}", flush=True)
                sftp.put(str(local), remote)
        finally:
            sftp.close()
    finally:
        transport.close()

    print(f"Done. Clients check: {base}/update.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
