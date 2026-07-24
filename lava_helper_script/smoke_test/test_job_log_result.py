# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Smoke test: fetch status, logs, and results for a single LAVA job.

Exercises the LavaClient methods that a triage workflow depends on:
    - get_job_status
    - get_job_logs      (windowed head/tail)
    - get_job_logs_plain
    - get_job_result    (json, junit, yaml, tap13, csv)

Credentials resolution order:
    1. --url / --token command-line options
    2. LAVA_URL / LAVA_TOKEN environment variables

Usage:
    python test_job_log_result.py                    # defaults to job 154480
    python test_job_log_result.py --job 154501
    python test_job_log_result.py --job 154501 --save-dir /tmp/lava-154501
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from lava_client import LavaAPIError, LavaAuthError, LavaClient


DEFAULT_JOB_ID = 154480
RESULT_FORMATS = ("json", "junit", "yaml", "tap13", "csv")
HEAD_LINES = 20
TAIL_LINES = 20


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch status, logs, and results for a LAVA job.",
    )
    p.add_argument("--url", default=os.environ.get("LAVA_URL"),
                   help="LAVA base URL (default: $LAVA_URL)")
    p.add_argument("--token", default=os.environ.get("LAVA_TOKEN"),
                   help="LAVA API token (default: $LAVA_TOKEN)")
    p.add_argument("--job", type=int, default=DEFAULT_JOB_ID,
                   help=f"Job id to fetch (default: {DEFAULT_JOB_ID})")
    p.add_argument("--save-dir", type=Path, default=None,
                   help="If set, write logs and each result format under this dir")
    p.add_argument("--head", type=int, default=HEAD_LINES,
                   help=f"Number of head lines to preview (default: {HEAD_LINES})")
    p.add_argument("--tail", type=int, default=TAIL_LINES,
                   help=f"Number of tail lines to preview (default: {TAIL_LINES})")
    return p.parse_args(argv)


def print_status(status: dict) -> None:
    """Print a human-friendly summary of the fields most useful for triage."""
    keys = ("id", "state", "health", "submitter", "actual_device",
            "requested_device_type", "priority", "submit_time",
            "start_time", "end_time", "failure_comment", "description")
    width = max(len(k) for k in keys)
    for k in keys:
        v = status.get(k)
        if v is None or v == "":
            continue
        print(f"  {k:<{width}} : {v}")


def preview_log_head(client: LavaClient, job_id: int, n: int) -> str:
    """Fetch the first n lines using the windowed endpoint (no ?start=0)."""
    return client.get_job_logs(job_id, start=0, end=n)


def preview_log_tail(full_log: str, n: int) -> str:
    """Return the last n lines of an already-fetched full log."""
    return "\n".join(full_log.splitlines()[-n:])


def save(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)


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

    # --- status --------------------------------------------------------------
    try:
        status = client.get_job_status(args.job)
    except LavaAPIError as exc:
        print(f"[status] {exc}", file=sys.stderr)
        return 1
    print(f"\n[status] {len(status)} fields returned")
    print_status(status)

    # --- logs ----------------------------------------------------------------
    try:
        head = preview_log_head(client, args.job, args.head)
    except LavaAPIError as exc:
        print(f"[logs head] {exc}", file=sys.stderr)
        return 1
    print(f"\n[logs head]  first {args.head} lines "
          f"({len(head.encode('utf-8', 'replace'))} bytes):")
    print(head.rstrip())

    try:
        full = client.get_job_logs_plain(args.job)
    except LavaAPIError as exc:
        print(f"[logs plain] {exc}", file=sys.stderr)
        return 1
    line_count = full.count("\n") + (0 if full.endswith("\n") else 1) if full else 0
    print(f"\n[logs plain] {len(full):,} bytes, ~{line_count:,} lines")

    print(f"\n[logs tail]  last {args.tail} lines:")
    print(preview_log_tail(full, args.tail))

    # --- results (all five formats) -----------------------------------------
    results: dict[str, list[dict] | bytes] = {}
    for fmt in RESULT_FORMATS:
        try:
            data = client.get_job_result(args.job, fmt=fmt)
        except LavaAPIError as exc:
            print(f"\n[result {fmt}] FAILED: {exc}", file=sys.stderr)
            continue
        results[fmt] = data
        if fmt == "json":
            passes = sum(1 for tc in data if tc.get("result") == "pass")
            fails = sum(1 for tc in data if tc.get("result") == "fail")
            skips = sum(1 for tc in data if tc.get("result") == "skip")
            others = len(data) - passes - fails - skips
            print(f"\n[result json]  {len(data)} test cases "
                  f"(pass={passes} fail={fails} skip={skips} other={others})")
            if fails and data:
                print("  first failing case:")
                for tc in data:
                    if tc.get("result") == "fail":
                        for k in ("name", "suite", "result", "unit", "measurement"):
                            if tc.get(k) not in (None, ""):
                                print(f"    {k}: {tc[k]}")
                        break
        else:
            size = len(data) if isinstance(data, bytes) else len(data.encode())
            print(f"[result {fmt:<5}] {size:,} bytes")

    # --- optional save-to-disk ----------------------------------------------
    if args.save_dir is not None:
        base = args.save_dir
        save(base / "status.txt",
             "\n".join(f"{k}: {v}" for k, v in status.items()) + "\n")
        save(base / "log.yaml", full)
        for fmt, data in results.items():
            suffix = {"json": "json", "junit": "xml", "yaml": "yaml",
                      "tap13": "tap", "csv": "csv"}[fmt]
            path = base / f"result.{suffix}"
            if fmt == "json":
                import json as _json
                save(path, _json.dumps(data, indent=2, default=str))
            else:
                save(path, data)
        print(f"\nsaved artefacts under: {base}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
