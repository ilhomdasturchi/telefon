"""
One-shot: ring the IP phone and play a short tone, then exit.

Usage:
  python call.py
  python call.py 101
  python call.py --direct          # dial phone IP immediately (no REGISTER wait)
  python call.py --tone 5          # play tone for 5 seconds after answer
"""

from __future__ import annotations

import argparse
import sys
import time

import config
from sip_pbx import SipPbx


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the registered IP phone once")
    parser.add_argument(
        "ext",
        nargs="?",
        default=config.PHONE_EXTENSION,
        help=f"extension to call (default {config.PHONE_EXTENSION})",
    )
    parser.add_argument("--bind", default=config.BIND_IP)
    parser.add_argument("--port", type=int, default=config.SIP_PORT)
    parser.add_argument(
        "--direct",
        action="store_true",
        help=f"call {config.PHONE_IP} immediately without waiting for REGISTER",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="seconds to wait for REGISTER before giving up (ignored with --direct)",
    )
    parser.add_argument("--tone", type=float, default=3.0, help="tone seconds after answer")
    parser.add_argument("--ring", type=float, default=25.0, help="max seconds to wait for answer")
    args = parser.parse_args()

    if args.ext not in config.ACCOUNTS:
        print(f"Unknown extension {args.ext}. Known: {', '.join(sorted(config.ACCOUNTS))}")
        return 1

    pbx = SipPbx(args.bind, args.port, config.ACCOUNTS, config.RTP_PORT)
    try:
        pbx.start()
    except OSError as exc:
        print(f"Could not bind {args.bind}:{args.port}: {exc}")
        print("Stop any other sip_pbx.py / ring.py process first.")
        return 1

    try:
        if args.direct:
            pbx.set_contact(args.ext, config.PHONE_IP, 5060)
            print(f"Direct dial {args.ext} @ {config.PHONE_IP}:5060")
        else:
            print(f"Waiting up to {args.timeout:.0f}s for {args.ext} to REGISTER...")
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                if any(r.username == args.ext for r in pbx.list_registrations()):
                    break
                time.sleep(0.25)
            else:
                # Phone often stays registered to this host; dial its known IP.
                print(
                    f"No REGISTER yet — dialing {config.PHONE_IP} directly "
                    f"(use --direct to skip the wait)."
                )
                pbx.set_contact(args.ext, config.PHONE_IP, 5060)

        ok = pbx.call_extension(
            args.ext,
            ring_seconds=args.ring,
            tone_seconds=args.tone,
        )
        return 0 if ok else 2
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        pbx.stop()


if __name__ == "__main__":
    sys.exit(main())
