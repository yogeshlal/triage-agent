# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: fetch and pretty-print status for a single LAVA job.

Exercises LavaClient.get_job_status() end-to-end. Prints every field the
server returned in the "id, state, health, ..." order that maps onto a
triage workflow, then dumps any remaining fields under "other".

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_job_status.py                    # defaults to job 154480
    python test_job_status.py --job 154501
    python test_job_status.py --job 154480 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_JOB_ID = 154480

# Ordered "triage-first" fields — printed in this order if present. Any
# remaining server fields are printed afterwards under "other".
PRIMARY_FIELDS = (
    "id",
    "state",
    "health",
    "submitter",
    "priority",
    "health_check",
    "actual_device",
    "requested_device_type",
    "submit_time",
    "start_time",
    "end_time",
    "description",
    "failure_comment",
    "failure_tags",
    "tags",
    "viewing_groups",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch and display status for a LAVA job.",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--job", type=int, default=DEFAULT_JOB_ID,
                   help=f"Job id to fetch (default: {DEFAULT_JOB_ID})")
    p.add_argument("--json", action="store_true",
                   help="Emit the raw server dict as JSON (for scripting)")
    return p.parse_args(argv)


def print_table(status: dict) -> None:
    width = max(len(k) for k in PRIMARY_FIELDS)

    print("primary fields")
    print("-" * (width + 2 + 40))
    for k in PRIMARY_FIELDS:
        if k not in status:
            continue
        v = status[k]
        if v is None or v == "" or v == []:
            v = "-"
        print(f"  {k:<{width}} : {v}")

    extras = {k: v for k, v in status.items() if k not in PRIMARY_FIELDS}
    if extras:
        print(f"\nother fields ({len(extras)})")
        print("-" * (width + 2 + 40))
        for k in sorted(extras):
            v = extras[k]
            preview = str(v)
            if len(preview) > 100:
                preview = preview[:97] + "..."
            print(f"  {k:<{width}} : {preview}")


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

    try:
        status = client.get_job_status(args.job)
    except LavaAPIError as exc:
        print(f"[status] {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(status, indent=2, default=str))
        return 0

    print(f"server: {args.url}")
    print(f"connected as: {client.whoami}")
    print(f"job id: {args.job}")
    print(f"fields returned: {len(status)}\n")
    print_table(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
