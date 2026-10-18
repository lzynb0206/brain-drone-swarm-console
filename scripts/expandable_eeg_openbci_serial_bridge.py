#!/usr/bin/env python3
"""Expose ExpandableEEG's native macOS RFCOMM link as a local pseudo serial port.

The official OpenBCI GUI can open the printed PTY path as a Cyton serial port.
All EEG bytes stay on the local machine; no LSL or network streamer is used.
"""

from __future__ import annotations

import argparse
import os
import pty
import select
import signal
import sys
import termios
import time
import tty
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.real.macos_rfcomm_serial import MacOSRFCOMMSerial  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bridge ExpandableEEG RFCOMM to a local PTY for official OpenBCI GUI."
    )
    parser.add_argument("--device-name", default="ExpandableEEGBT")
    parser.add_argument("--channel", type=int, default=1)
    return parser


def _set_raw(fd: int) -> None:
    tty.setraw(fd, when=termios.TCSANOW)


def main() -> int:
    args = _parser().parse_args()
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    master_fd, slave_fd = pty.openpty()
    _set_raw(master_fd)
    _set_raw(slave_fd)
    slave_path = os.ttyname(slave_fd)
    rfcomm: MacOSRFCOMMSerial | None = None
    configured_for_stream = False
    bytes_to_gui = 0
    bytes_to_device = 0

    try:
        rfcomm = MacOSRFCOMMSerial(
            device_name=args.device_name,
            channel=args.channel,
            timeout=0.01,
            write_timeout=2.0,
        )
        print(f"PTY={slave_path}", flush=True)
        print(
            f"READY device={args.device_name} channel={args.channel} local_only=true",
            flush=True,
        )
        last_report = time.monotonic()
        while not stopping:
            readable, _, _ = select.select([master_fd], [], [], 0.005)
            if readable:
                command = os.read(master_fd, 4096)
                if not command:
                    break
                # The vendor BT firmware needs the captured Windows startup
                # sequence before its first stream command.  Preserve every GUI
                # command, but insert d/c immediately before the first b.
                if b"b" in command and not configured_for_stream:
                    prefix, suffix = command.split(b"b", 1)
                    if prefix:
                        bytes_to_device += rfcomm.write(prefix)
                    bytes_to_device += rfcomm.write(b"d")
                    time.sleep(2.0)
                    bytes_to_device += rfcomm.write(b"c")
                    time.sleep(2.0)
                    bytes_to_device += rfcomm.write(b"b" + suffix)
                    configured_for_stream = True
                else:
                    bytes_to_device += rfcomm.write(command)

            waiting = rfcomm.in_waiting
            if waiting:
                payload = rfcomm.read(min(waiting, 65_536))
                if payload:
                    os.write(master_fd, payload)
                    bytes_to_gui += len(payload)

            now = time.monotonic()
            if now - last_report >= 5.0:
                print(
                    f"HEALTH to_gui={bytes_to_gui} to_device={bytes_to_device}",
                    flush=True,
                )
                last_report = now
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if rfcomm is not None:
            try:
                rfcomm.write(b"s")
            except Exception:
                pass
            rfcomm.close()
        os.close(master_fd)
        os.close(slave_fd)
        print(
            f"STOPPED to_gui={bytes_to_gui} to_device={bytes_to_device}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
