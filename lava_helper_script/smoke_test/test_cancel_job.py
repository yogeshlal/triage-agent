# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: cancel a single LAVA job.

Exercises LavaClient.cancel_job() end-to-end:
    1. fetch status before cancel
    2. send cancel signal (GET jobs/<id>/cancel/)
    3. fetch status after cancel and diff state/health

The server is idempotent — cancelling an already-Finished or already-
Canceling job still returns 200 with "Job cancel signal sent." Callers
should always re-check status to know the real outcome, which this
script does automatically.

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_cancel_job.py                       # defaults to job 154505
    python test_cancel_job.py --job 154520
    python test_cancel_job.py --job 154505 --dry-run       # status-only, no cancel
    python test_cancel_job.py --job 154505 --yes           # skip confirmation
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_JOB_ID = 154505
TERMINAL_STATES = ("Finished",)  # cancel on Finished is a documented no-op


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Send a cancel signal to a LAVA job.",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--job", type=int, default=DEFAULT_JOB_ID,
                   help=f"Job id to cancel (default: {DEFAULT_JOB_ID})")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch status but do NOT send cancel")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the interactive confirmation prompt")
    p.add_argument("--recheck-delay", type=float, default=1.0,
                   help="Seconds to wait before re-fetching status "
                        "(default: 1.0)")
    return p.parse_args(argv)


def print_status_summary(label: str, status: dict) -> None:
    print(f"[{label}]")
    for k in ("id", "state", "health", "actual_device",
             "requested_device_type", "start_time", "end_time",
             "failure_comment"):
        v = status.get(k)
        if v not in (None, "", []):
            print(f"  {k:<24} : {v}")


def diff_states(before: dict, after: dict) -> None:
    changed = []
    for k in ("state", "health", "end_time", "failure_comment"):
        if before.get(k) != after.get(k):
            changed.append((k, before.get(k), after.get(k)))
    if not changed:
        print("(no state/health change observed yet — cancel may still be "
              "propagating; re-run status later)")
        return
    print("changed fields:")
    for k, b, a in changed:
        print(f"  {k}: {b!r} -> {a!r}")


def confirm(job_id: int, state: str, health: str) -> bool:
    prompt = (f"About to cancel job {job_id} (state={state}, health={health}). "
              f"Proceed? [y/N] ")
    try:
        reply = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return reply in ("y", "yes")


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
    print(f"job id: {args.job}\n")

    # --- BEFORE --------------------------------------------------------------
    try:
        before = client.get_job_status(args.job)
    except LavaAPIError as exc:
        print(f"[status before] {exc}", file=sys.stderr)
        return 1
    print_status_summary("before", before)

    state = str(before.get("state", ""))
    health = str(before.get("health", ""))

    if state in TERMINAL_STATES:
        print(f"\nnote: job is already in terminal state {state!r}; "
              "cancel is a documented no-op on the server side.")

    if args.dry_run:
        print("\n--dry-run: not sending cancel signal.")
        return 0

    if not args.yes and not confirm(args.job, state, health):
        print("cancel aborted by user.")
        return 0

    # --- CANCEL --------------------------------------------------------------
    try:
        reply = client.cancel_job(args.job)
    except LavaAPIError as exc:
        print(f"\n[cancel] {exc}", file=sys.stderr)
        return 1
    print(f"\n[cancel] server reply: {reply}")

    # --- AFTER ---------------------------------------------------------------
    if args.recheck_delay > 0:
        time.sleep(args.recheck_delay)

    try:
        after = client.get_job_status(args.job)
    except LavaAPIError as exc:
        print(f"[status after] {exc}", file=sys.stderr)
        return 1
    print()
    print_status_summary("after", after)
    print()
    diff_states(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
