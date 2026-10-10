"""
Convenience launcher: start the PBX and automatically ring the phone
once it registers (or immediately if already registered).
"""

from __future__ import annotations

import argparse
import time

import config
from sip_pbx import SipPbx


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for phone REGISTER, then ring it")
    parser.add_argument("--bind", default=config.BIND_IP)
    parser.add_argument("--port", type=int, default=config.SIP_PORT)
    parser.add_argument("--ext", default=config.PHONE_EXTENSION)
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for REGISTER")
    args = parser.parse_args()

    pbx = SipPbx(args.bind, args.port, config.ACCOUNTS, config.RTP_PORT)
    pbx.start()
    print()
    print("Configure the phone:")
    print(f"  SIP server : {args.bind}:{args.port}")
    print(f"  User / pass: {args.ext} / {config.ACCOUNTS[args.ext]}")
    print(f"  Transport  : UDP")
    print()
    print(f"Waiting up to {args.timeout:.0f}s for extension {args.ext} to register...")

    deadline = time.time() + args.timeout
    try:
        while time.time() < deadline:
            regs = {r.username: r for r in pbx.list_registrations()}
            if args.ext in regs:
                print(f"Got REGISTER from {args.ext}. Calling now...")
                time.sleep(0.5)
                pbx.call_extension(args.ext)
                break
            time.sleep(0.5)
        else:
            print("Timed out waiting for REGISTER.")
            print("Check phone SIP settings and that Windows Firewall allows UDP 5060.")
    except KeyboardInterrupt:
        print()
    finally:
        pbx.stop()


if __name__ == "__main__":
    main()
