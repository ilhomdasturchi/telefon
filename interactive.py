"""
Interactive SIP console for the desk phone.

1. Starts the local PBX and waits until the phone REGISTERs.
2. Then you can call, answer, reject, hang up from this terminal.

Usage:
  python interactive.py

Commands (after register):
  call [ext]     Call extension (default: phone 101)
  answer / a     Pick up an incoming call from the phone
  reject / busy  Decline an incoming call
  hangup / h     End or cancel the current call
  tone [sec]     Play a beep on the active call
  status / s     Show registrations and call state
  help           Show commands
  quit / q       Exit
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import config
from sip_pbx import SipPbx


HELP = """
commands:
  call [ext]     dial the phone (default {ext})
  answer         pick up incoming call
  reject         decline incoming call
  hangup         end / cancel call
  tone [sec]     beep on active call (default 2s)
  status         registrations + call state
  help           this text
  quit           exit
""".format(ext=config.PHONE_EXTENSION)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive SIP phone console")
    parser.add_argument("--bind", default=config.BIND_IP)
    parser.add_argument("--port", type=int, default=config.SIP_PORT)
    parser.add_argument("--ext", default=config.PHONE_EXTENSION, help="desk phone extension")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for REGISTER before giving up",
    )
    args = parser.parse_args()

    if not sys.stdin.isatty():
        print("This script needs an interactive terminal.")
        return 1

    pbx = SipPbx(
        args.bind,
        args.port,
        config.ACCOUNTS,
        config.RTP_PORT,
        auto_answer=False,
    )
    try:
        pbx.start()
    except OSError as exc:
        print(f"Could not bind {args.bind}:{args.port}: {exc}")
        print("Stop any other sip_pbx.py / call.py / ring.py process first.")
        return 1

    print()
    print("Waiting for desk phone to REGISTER...")
    print(f"  server : {args.bind}:{args.port}")
    print(f"  expect : extension {args.ext}")
    print()

    if not pbx.wait_for_register(args.ext, timeout=args.timeout):
        print("Timed out waiting for REGISTER.")
        print("Check the phone SIP settings / Embedded Web, then try again.")
        pbx.stop()
        return 2

    print()
    print(f"Phone {args.ext} is registered. Interactive mode ready.")
    print(HELP)
    print("Tip: from the desk phone, dial any number / pbx to call this console.")
    print()

    dial_lock = threading.Lock()

    def dial_async(target: str) -> None:
        if not dial_lock.acquire(blocking=False):
            print("[call] dial already in progress")
            return

        def worker() -> None:
            try:
                pbx.dial(target, ring_seconds=60.0, auto_hangup_after_tone=0.0)
            finally:
                dial_lock.release()

        threading.Thread(target=worker, name="dial", daemon=True).start()

    try:
        while True:
            try:
                line = input("phone> ").strip()
            except EOFError:
                print()
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            rest = parts[1:]

            if cmd in {"q", "quit", "exit"}:
                break
            if cmd in {"help", "?"}:
                print(HELP)
                continue
            if cmd in {"s", "status", "reg", "regs"}:
                regs = pbx.list_registrations()
                if not regs:
                    print("  registrations: (none)")
                else:
                    print("  registrations:")
                    for reg in regs:
                        print(f"    {reg.username} -> {reg.endpoint[0]}:{reg.endpoint[1]}")
                print(f"  call: {pbx.call_status()}")
                continue
            if cmd in {"a", "answer", "pickup", "pick"}:
                pbx.answer()
                continue
            if cmd in {"reject", "busy", "decline"}:
                pbx.reject()
                continue
            if cmd in {"h", "hangup", "bye", "cancel"}:
                pbx.hangup()
                continue
            if cmd == "tone":
                seconds = 2.0
                if rest:
                    try:
                        seconds = float(rest[0])
                    except ValueError:
                        print("usage: tone [seconds]")
                        continue
                pbx.play_tone(seconds)
                continue
            if cmd in {"call", "dial", "c"}:
                target = rest[0] if rest else args.ext
                dial_async(target)
                continue

            print(f"unknown command: {cmd}  (type 'help')")
    except KeyboardInterrupt:
        print()
    finally:
        pbx.stop()
        print("bye")

    return 0


if __name__ == "__main__":
    sys.exit(main())
