# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""System prompt construction for the triage agent.

The system prompt is deliberately compact: SKILL.md (~2.7 K) inline, plus
directory listings of references/ and assets/ so the model discovers the CoT
catalog without loading 300 K of markdown. references/triage.md and any
cots-*.md are fetched via read_file at run time.
"""

from __future__ import annotations

import os
from pathlib import Path

from .kernelci import NodeContext


def lava_job_url(job_id: str) -> str | None:
    """Compose the full LAVA job page URL from LAVA_URL and job_id.

    Returns None when LAVA_URL is unset — the prompt falls back to bare id.
    """
    base = (os.getenv("LAVA_URL") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/scheduler/job/{job_id}"


def _list_dir_lines(directory: Path) -> list[str]:
    if not directory.exists():
        return [f"  (missing directory: {directory})"]
    lines: list[str] = []
    for child in sorted(directory.iterdir()):
        try:
            stat = child.stat()
        except OSError:
            continue
        kind = "dir" if child.is_dir() else "file"
        size = f"{stat.st_size:>8}" if child.is_file() else "     dir"
        lines.append(f"  {size}  {kind:<4}  {child.name}")
    return lines


def build_system_prompt(
    *,
    skill_dir: Path,
    log_path: Path,
    log_size_bytes: int,
    log_line_count: int,
    log_truncated: bool,
    report_path: Path,
    job_id: str,
    node_context: NodeContext | None = None,
    node_json_path: Path | None = None,
) -> str:
    skill_md_path = skill_dir / "SKILL.md"
    skill_md = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else \
        "(SKILL.md missing — proceed with best-effort triage.)"

    references_dir = skill_dir / "references"
    assets_dir = skill_dir / "assets"

    truncation_note = (
        f"\nNOTE: The log was truncated to {log_size_bytes} bytes; the tail is missing."
        if log_truncated else ""
    )
    lava_url = lava_job_url(job_id)
    lava_url_line = (
        f"- LAVA job URL       : {lava_url}"
        if lava_url
        else "- LAVA job URL       : (LAVA_URL not set)"
    )

    # Only a handful of scalars go inline; the full node JSON sits in the run
    # workspace and is read on demand, keeping the fixed prompt prefix stable
    # for caching.
    kernelci_parts: list[str] = []
    if node_context is not None:
        kernelci_parts = [
            "",
            "# KernelCI context for this run",
            "The KernelCI-side view of this job, fetched from the KernelCI API:",
            *node_context.summary_lines(),
        ]
        if node_json_path is not None:
            kernelci_parts.append(
                f"- Full node JSON     : `{node_json_path}`"
            )
            kernelci_parts.append(
                "  Read that file for artifacts, timestamps and parent node ids."
            )
        kernelci_parts.append(
            "Use this to separate an environment or infrastructure problem from a"
        )
        kernelci_parts.append(
            "genuine kernel regression, and name the tree, branch and commit in"
        )
        kernelci_parts.append("the report.")

    parts = [
        "# Role",
        "You are a Linux kernel triage agent. You have file-read tools available.",
        "Use them to explore the skill directory and the LAVA log, then produce a",
        "root-cause report by calling `write_report`.",
        "",
        "# Tools",
        "- `read_file(path, offset=0, limit=400)` — line-based windowed read.",
        "- `list_dir(path)` — non-recursive listing.",
        "- `grep(path, pattern, max_matches=50, context=2)` — regex over a file, streamed.",
        "- `write_report(content)` — write the final Markdown report. Terminator.",
        "",
        "# Skill contract",
        "The following is the verbatim contents of SKILL.md. Follow its instructions.",
        "",
        "```markdown",
        skill_md.rstrip(),
        "```",
        "",
        "# Available skill files",
        f"Skill directory: {skill_dir}",
        "",
        f"{skill_dir}/references/:",
        *_list_dir_lines(references_dir),
        "",
        f"{skill_dir}/assets/:",
        *_list_dir_lines(assets_dir),
        "",
        "# Parameters for this run",
        f"- `dump_location`     = `{log_path}`   (single plain-text LAVA log)",
        f"- `report_file`       = `{report_path}`",
        "- `windows_unc_path`  = null",
        "- `jira_url`          = null",
        f"- LAVA job id        : {job_id}",
        lava_url_line,
        f"- Log size           : {log_size_bytes} bytes",
        f"- Log line count     : {log_line_count}"
        + truncation_note,
        *kernelci_parts,
        "",
        "# Required first action",
        f"Read `{references_dir}/triage.md` in full via `read_file` before doing",
        "anything else. Follow its CoT-selection logic to pick the relevant",
        "`cots-*.md` file(s) and apply them to this log.",
        "",
        "# Log analysis strategy",
        "The log can be very large. Do NOT try to read it end-to-end. Instead:",
        "1. Use `grep` to locate crash signatures: `Kernel panic`, `Unable to handle`,",
        "   `Call trace:`, `BUG:`, `WARNING:`, `Oops`, `Internal error`, `PC is at`,",
        "   `rcu:`, `hung task`, or anything the selected CoT recommends.",
        "2. Use `read_file` with `offset`/`limit` to pull a window around each hit.",
        "3. Cross-reference with device tree / driver / subsystem CoTs as needed.",
        "",
        "# Termination",
        "Finish by calling `write_report` exactly once with the full Markdown report",
        "conforming to `assets/report-template.md`. In the Executive Summary include",
        "`**LAVA Job URL:**` set to the full URL shown above (`LAVA job URL`),",
        "not just the numeric id. After write_report succeeds,",
        "stop — do NOT emit any additional prose in your final assistant turn.",
    ]
    return "\n".join(parts)


def build_user_message(*, job_id: str, log_path: Path, report_path: Path) -> str:
    return (
        f"Triage LAVA job {job_id}. Log file: {log_path}. "
        f"Write the final Markdown report by calling `write_report` with the report body. "
        f"It will be persisted to {report_path}."
    )
