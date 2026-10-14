#!/usr/bin/env python3
"""Inspect or configure an ExpandableEEG through its Type-C serial port."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adapters.real import ExpandableEEGConfigurator  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="macOS/Linux replacement for ExpandableEEG_Config.exe",
    )
    parser.add_argument("--port", required=True, help="Type-C USB configuration serial port")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--set-bt", metavar="NAME", help="switch to Bluetooth mode and set the device name",
    )
    action.add_argument(
        "--set-ap", nargs=2, metavar=("SSID", "PASSWORD"), help="switch to Wi-Fi AP mode",
    )
    action.add_argument(
        "--set-client", nargs=2, metavar=("SSID", "PASSWORD"), help="switch to Wi-Fi client mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configurator = ExpandableEEGConfigurator()
    try:
        if args.set_bt is not None:
            previous = configurator.configure_bluetooth(args.port, args.set_bt)
            target = f"Bluetooth ({args.set_bt})"
        elif args.set_ap is not None:
            previous = configurator.configure_wifi_ap(args.port, *args.set_ap)
            target = f"WiFi AP ({args.set_ap[0]})"
        elif args.set_client is not None:
            previous = configurator.configure_wifi_client(args.port, *args.set_client)
            target = f"WiFi Client ({args.set_client[0]})"
        else:
            info = configurator.probe(args.port)
            print(
                f"mode={info.mode_name} channels={info.channel_count} "
                f"network={info.network_name or '-'} ip={info.ip_address or '-'}"
            )
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"configuration sent: {previous.mode_name} -> {target}; "
        "wait for the device to reboot before reconnecting"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
