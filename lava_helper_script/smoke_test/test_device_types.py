# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: list device types from a LAVA server.

Credentials resolution order (highest first):
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Prints one row per device-type with the full state x health count matrix.
"""

from __future__ import annotations

import argparse
import os
import sys

from lava_client import LavaAPIError, LavaAuthError, LavaClient


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List LAVA device-types with state x health counts.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("LAVA_URL"),
        help="LAVA base URL (default: $LAVA_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("LAVA_TOKEN"),
        help="LAVA API token (default: $LAVA_TOKEN)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.url:
        print("error: --url or LAVA_URL is required", file=sys.stderr)
        return 2
    if not args.token:
        print("error: --token or LAVA_TOKEN is required", file=sys.stderr)
        return 2

    try:
        client = LavaClient(url=args.url, token=args.token)
    except LavaAuthError as exc:
        print(f"[auth] {exc}", file=sys.stderr)
        return 2

    print(f"server: {args.url}")
    print(f"connected as: {client.whoami}")

    try:
        device_types = client.get_device_types()
    except LavaAPIError as exc:
        print(f"[api] {exc}", file=sys.stderr)
        return 1

    print(f"device types: {len(device_types)}")
    print()

    header = f"{'name':<28} {'arch':<10} {'total':>5}  {'by_state':<40} {'by_health'}"
    print(header)
    print("-" * len(header))
    for dt in sorted(device_types, key=lambda d: d.get("name") or ""):
        name = dt.get("name") or "?"
        arch = dt.get("architecture") or "-"
        total = dt.get("total", 0)
        by_state = ",".join(f"{k}={v}" for k, v in dt["by_state"].items() if v)
        by_health = ",".join(f"{k}={v}" for k, v in dt["by_health"].items() if v)
        print(f"{name:<28} {arch:<10} {total:>5}  {by_state:<40} {by_health}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
