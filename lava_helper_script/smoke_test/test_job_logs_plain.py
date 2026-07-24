# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: fetch the full log for a single LAVA job in one shot.

Exercises LavaClient.get_job_logs_plain() end-to-end. Reports total size,
line count, elapsed wall time, and average throughput. Prints a small
head/tail preview so the caller can eyeball that the log is well-formed
and streams from start to finish. Optionally saves the full log to disk.

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_job_logs_plain.py                       # defaults to job 154480
    python test_job_logs_plain.py --job 154501
    python test_job_logs_plain.py --job 154480 --preview 5
    python test_job_logs_plain.py --job 154480 --save /tmp/154480.log
    python test_job_logs_plain.py --job 154480 --quiet   # size line only
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_JOB_ID = 154480
DEFAULT_PREVIEW = 10


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch the full log for a LAVA job (get_job_logs_plain).",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--job", type=int, default=DEFAULT_JOB_ID,
                   help=f"Job id to fetch (default: {DEFAULT_JOB_ID})")
    p.add_argument("--preview", type=int, default=DEFAULT_PREVIEW,
                   help=f"Head/tail preview lines (default: {DEFAULT_PREVIEW}, "
                        "set 0 to disable)")
    p.add_argument("--save", type=Path, default=None,
                   help="Write the full log to this path")
    p.add_argument("--quiet", action="store_true",
                   help="Print only the one-line size summary")
    return p.parse_args(argv)


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


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

    if not args.quiet:
        print(f"server: {args.url}")
        print(f"connected as: {client.whoami}")
        print(f"job id: {args.job}")

    t0 = time.monotonic()
    try:
        full = client.get_job_logs_plain(args.job)
    except LavaAPIError as exc:
        print(f"[logs plain] {exc}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - t0

    size_bytes = len(full.encode("utf-8", "replace"))
    lines = count_lines(full)
    throughput = size_bytes / elapsed if elapsed > 0 else 0.0

    print(f"\n[logs plain] {size_bytes:,} bytes ({human_bytes(size_bytes)}), "
          f"{lines:,} lines, {elapsed:.2f}s, "
          f"{human_bytes(int(throughput))}/s")

    if args.quiet:
        return 0

    if args.preview > 0 and lines:
        head_lines = full.splitlines()[:args.preview]
        print(f"\n[head]  first {len(head_lines)} lines:")
        print("\n".join(head_lines))

        tail_n = min(args.preview, lines)
        tail_lines = full.splitlines()[-tail_n:]
        # Skip printing tail if the log is short enough that head already
        # showed the same lines.
        if lines > args.preview:
            print(f"\n[tail]  last {tail_n} lines:")
            print("\n".join(tail_lines))

    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(full)
        print(f"\nsaved full log to: {args.save} "
              f"({human_bytes(args.save.stat().st_size)})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
