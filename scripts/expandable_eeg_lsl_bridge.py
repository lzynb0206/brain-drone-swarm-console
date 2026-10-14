#!/usr/bin/env python3
"""Bridge ExpandableEEG Bluetooth samples to an 8-channel LSL EEG stream.

This process is intentionally independent from the operator UI.  It lets the
soldier console consume ``LSL · obci_eeg1`` while the native macOS RFCOMM
adapter owns the Bluetooth device.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters.real import ExpandableEEGSerialStream
from src.services import LSLEEGOutlet


DEFAULT_PORT = "/dev/cu.ExpandableEEGBT"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish ExpandableEEG Bluetooth data as LSL name=obci_eeg1."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--name", default="obci_eeg1")
    parser.add_argument("--frame-size", type=int, default=32)
    return parser


def main() -> int:
    args = _parser().parse_args()
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    stream = ExpandableEEGSerialStream(
        serial_port=args.port,
        frame_size=max(1, args.frame_size),
    )
    outlet = LSLEEGOutlet(
        stream_name=args.name,
        source_id="expandable-eeg-native-rfcomm",
    )
    last_report = time.monotonic()
    published = 0

    print(f"Connecting ExpandableEEG on {args.port} ...", flush=True)
    try:
        stream.connect()
        outlet.start(stream.channel_names, stream.sample_rate_hz, ("uV",) * 8)
        print(
            f"LIVE name={args.name} type=EEG channels=8 rate={stream.sample_rate_hz:.0f}Hz "
            f"source={stream.source_name}",
            flush=True,
        )
        while not stopping:
            try:
                frame = stream.read_frame()
            except TimeoutError:
                continue
            published += outlet.push_frame(frame)
            now = time.monotonic()
            if now - last_report >= 5.0:
                health = stream.health_snapshot()
                print(
                    "HEALTH "
                    f"samples={published} valid={health['valid_packets']} "
                    f"invalid={health['invalid_packets']} gaps={health['gap_count']}",
                    flush=True,
                )
                last_report = now
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        outlet.stop()
        stream.disconnect()
        print(f"STOPPED samples={published}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
