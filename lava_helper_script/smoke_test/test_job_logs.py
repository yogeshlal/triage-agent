# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: fetch logs for a single LAVA job.

Exercises the two log-fetch methods on LavaClient:
    - get_job_logs(start, end)   — windowed
    - get_job_logs_plain()       — full log, single request

Prints head/tail previews and total size. Optionally saves the full log
and a windowed slice to disk.

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_job_logs.py                       # defaults to job 154480
    python test_job_logs.py --job 154501
    python test_job_logs.py --job 154480 --head 50 --tail 50
    python test_job_logs.py --job 154480 --start 100 --end 200
    python test_job_logs.py --job 154480 --save /tmp/lava-154480.log
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_JOB_ID = 154480
DEFAULT_HEAD = 20
DEFAULT_TAIL = 20


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch logs for a LAVA job (plain + windowed).",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--job", type=int, default=DEFAULT_JOB_ID,
                   help=f"Job id to fetch (default: {DEFAULT_JOB_ID})")
    p.add_argument("--head", type=int, default=DEFAULT_HEAD,
                   help=f"Head preview lines (default: {DEFAULT_HEAD})")
    p.add_argument("--tail", type=int, default=DEFAULT_TAIL,
                   help=f"Tail preview lines (default: {DEFAULT_TAIL})")
    p.add_argument("--start", type=int, default=None,
                   help="Explicit start line for windowed fetch")
    p.add_argument("--end", type=int, default=None,
                   help="Explicit end line for windowed fetch")
    p.add_argument("--save", type=Path, default=None,
                   help="Write the full log to this path")
    p.add_argument("--no-plain", action="store_true",
                   help="Skip the full-log fetch (windowed head/tail only)")
    return p.parse_args(argv)


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


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
    print(f"job id: {args.job}")

    # --- windowed head -------------------------------------------------------
    try:
        head = client.get_job_logs(args.job, start=0, end=args.head)
    except LavaAPIError as exc:
        print(f"[logs head] {exc}", file=sys.stderr)
        return 1
    print(f"\n[windowed head]  start=0, end={args.head}  "
          f"-> {len(head.encode('utf-8', 'replace')):,} bytes, "
          f"{count_lines(head)} lines")
    print(head.rstrip())

    # --- optional explicit window --------------------------------------------
    if args.start is not None or args.end is not None:
        start = args.start or 0
        try:
            window = client.get_job_logs(args.job, start=start, end=args.end)
        except LavaAPIError as exc:
            print(f"[logs window] {exc}", file=sys.stderr)
            return 1
        print(f"\n[windowed slice] start={start}, end={args.end}  "
              f"-> {len(window.encode('utf-8', 'replace')):,} bytes, "
              f"{count_lines(window)} lines")
        print(window.rstrip())

    # --- full log (skip on --no-plain) ---------------------------------------
    if args.no_plain:
        return 0

    try:
        full = client.get_job_logs_plain(args.job)
    except LavaAPIError as exc:
        print(f"[logs plain] {exc}", file=sys.stderr)
        return 1

    total_lines = count_lines(full)
    print(f"\n[plain full]     {len(full):,} bytes, {total_lines:,} lines")

    # --- windowed tail -------------------------------------------------------
    lines = full.splitlines()
    tail = "\n".join(lines[-args.tail:]) if lines else ""
    print(f"\n[windowed tail]  last {min(args.tail, total_lines)} lines "
          f"of the full log:")
    print(tail)

    # --- optional save-to-disk ----------------------------------------------
    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(full)
        print(f"\nsaved full log to: {args.save}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
