# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: list the most recent jobs submitted by a given user.

Exercises LavaClient.list_jobs(submitter=..., limit=...) end-to-end.
Default submitter is `kernel_ci` and default limit is 10. Prints a
tabular summary (id, state, health, device, submit time, description).

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_list_jobs_by_submitter.py                        # kernel_ci, 10
    python test_list_jobs_by_submitter.py --submitter someone
    python test_list_jobs_by_submitter.py --limit 25
    python test_list_jobs_by_submitter.py --state FINISHED --health INCOMPLETE
    python test_list_jobs_by_submitter.py --json                 # raw JSON to stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_SUBMITTER = "kernel_ci"
DEFAULT_LIMIT = 10


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="List the most recent jobs submitted by a given user.",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--submitter", default=DEFAULT_SUBMITTER,
                   help=f"Submitter username (default: {DEFAULT_SUBMITTER!r})")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"Max jobs to fetch (default: {DEFAULT_LIMIT})")
    p.add_argument("--state", default=None,
                   help="Optional state filter (SUBMITTED, SCHEDULING, "
                        "SCHEDULED, RUNNING, CANCELING, FINISHED)")
    p.add_argument("--health", default=None,
                   help="Optional health filter (UNKNOWN, COMPLETE, "
                        "INCOMPLETE, CANCELED)")
    p.add_argument("--device-type", default=None,
                   help="Optional requested_device_type filter")
    p.add_argument("--json", action="store_true",
                   help="Emit the raw list as JSON (for scripting)")
    return p.parse_args(argv)


def truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[: max(0, width - 1)] + "…"


def print_table(jobs: list[dict], submitter: str) -> None:
    if not jobs:
        print(f"no jobs found for submitter={submitter!r}")
        return

    cols = [
        ("id",             8, lambda j: str(j.get("id", "?"))),
        ("state",         10, lambda j: str(j.get("state", "?"))),
        ("health",        11, lambda j: str(j.get("health", "?"))),
        ("device",        22, lambda j: str(j.get("actual_device")
                                            or j.get("requested_device_type")
                                            or "-")),
        ("submit_time",   20, lambda j: str(j.get("submit_time", "-"))[:19]),
        ("description",   60, lambda j: str(j.get("description") or "-")),
    ]

    header = "  ".join(f"{name:<{w}}" for name, w, _ in cols)
    print(header)
    print("-" * len(header))
    for j in jobs:
        row = "  ".join(f"{truncate(fn(j), w):<{w}}" for _, w, fn in cols)
        print(row)


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

    kwargs: dict = {"submitter": args.submitter, "limit": args.limit}
    if args.state:
        kwargs["state"] = args.state
    if args.health:
        kwargs["health"] = args.health
    if args.device_type:
        kwargs["device_type"] = args.device_type

    try:
        jobs = client.list_jobs(**kwargs)
    except (LavaAPIError, ValueError) as exc:
        print(f"[list_jobs] {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(jobs, indent=2, default=str))
        return 0

    print(f"server: {args.url}")
    print(f"connected as: {client.whoami}")
    filters = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"filters: {filters}")
    print(f"jobs returned: {len(jobs)}\n")
    print_table(jobs, args.submitter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
