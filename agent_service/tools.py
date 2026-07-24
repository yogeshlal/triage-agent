# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Sandboxed file tools exposed to the agent.

Path resolution rules
---------------------
1. Relative paths resolve against `ctx.run_workspace`.
2. Absolute paths are used as given.
3. Every path is then `resolve(strict=True)`d — this follows symlinks and
   requires the target to exist.
4. The real path must live under `ctx.skill_dir` OR `ctx.run_workspace`.
   Anything else raises `ToolError`, which is caught by `dispatch()` and
   returned to the model as an error string (never bubbles to HTTP 500).

`write_report` is special: it ignores any path argument and writes to the
runner-fixed `ctx.report_path`. Path-traversal via tool args is impossible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_READ_LIMIT = 2000
MAX_GREP_MATCHES = 500
MAX_FILE_BYTES = 200 * 1024 * 1024


class ToolError(Exception):
    """Raised inside a tool and surfaced as an error string to the model."""


@dataclass
class AgentContext:
    skill_dir: Path
    run_workspace: Path
    report_path: Path
    report_written: bool = field(default=False)


def _resolve_and_check(path_str: str, ctx: AgentContext) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = ctx.run_workspace / p
    try:
        real = p.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ToolError(f"path not found: {path_str}") from exc
    except OSError as exc:
        raise ToolError(f"cannot resolve {path_str}: {exc}") from exc

    roots = [ctx.skill_dir.resolve(), ctx.run_workspace.resolve()]
    if not any(real == r or r in real.parents for r in roots):
        raise ToolError(
            f"path {path_str} is outside allowed roots "
            f"(skill_dir={ctx.skill_dir}, run_workspace={ctx.run_workspace})"
        )
    return real


def read_file(path: str, offset: int = 0, limit: int = 400,
              ctx: AgentContext | None = None) -> dict[str, Any]:
    assert ctx is not None
    if offset < 0:
        raise ToolError("offset must be >= 0")
    limit = max(1, min(limit, MAX_READ_LIMIT))

    real = _resolve_and_check(path, ctx)
    if not real.is_file():
        raise ToolError(f"not a file: {path}")
    if real.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"file too large: {real.stat().st_size} bytes > {MAX_FILE_BYTES}")

    lines: list[str] = []
    total_lines = 0
    eof = True
    with real.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            total_lines = i + 1
            if i < offset:
                continue
            if len(lines) < limit:
                lines.append(line)
            elif len(lines) == limit:
                eof = False
                # keep counting total_lines
        # Fell through: count all lines. If we filled limit but never
        # entered the `elif`, the file ended exactly at offset+limit.
        if len(lines) == limit and total_lines > offset + limit:
            eof = False

    return {
        "path": str(real),
        "start_line": offset,
        "end_line": offset + len(lines),
        "total_lines": total_lines,
        "eof": eof,
        "content": "".join(lines),
    }


def list_dir(path: str, ctx: AgentContext | None = None) -> dict[str, Any]:
    assert ctx is not None
    real = _resolve_and_check(path, ctx)
    if not real.is_dir():
        raise ToolError(f"not a directory: {path}")

    entries: list[dict[str, Any]] = []
    for child in sorted(real.iterdir()):
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size_bytes": stat.st_size if child.is_file() else None,
        })
    return {"path": str(real), "entries": entries}


def grep(path: str, pattern: str, max_matches: int = 50, context: int = 2,
         ctx: AgentContext | None = None) -> dict[str, Any]:
    assert ctx is not None
    max_matches = max(1, min(max_matches, MAX_GREP_MATCHES))
    context = max(0, min(context, 20))

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex: {exc}") from exc

    real = _resolve_and_check(path, ctx)
    if not real.is_file():
        raise ToolError(f"not a file: {path}")

    matches: list[dict[str, Any]] = []
    ring: list[str] = []
    pending: list[tuple[int, dict[str, Any]]] = []
    with real.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            # Fill trailing context on previous matches
            for entry in pending:
                entry[1]["context_after"].append(line.rstrip("\n"))
            pending = [e for e in pending if len(e[1]["context_after"]) < context]

            if regex.search(line):
                if len(matches) >= max_matches:
                    matches.append({"__truncated__": True})
                    break
                entry = {
                    "line_no": lineno,
                    "line": line.rstrip("\n"),
                    "context_before": list(ring),
                    "context_after": [],
                }
                matches.append(entry)
                pending.append((lineno, entry))

            ring.append(line.rstrip("\n"))
            if len(ring) > context:
                ring.pop(0)

    truncated = bool(matches) and matches[-1].get("__truncated__")
    if truncated:
        matches.pop()

    return {
        "path": str(real),
        "pattern": pattern,
        "match_count": len(matches),
        "truncated": bool(truncated),
        "matches": matches,
    }


def write_report(content: str, ctx: AgentContext | None = None) -> dict[str, Any]:
    assert ctx is not None
    if ctx.report_written:
        return {"ok": False, "error": "report already written; further calls ignored"}
    if not content or not content.strip():
        raise ToolError("report content is empty")

    ctx.report_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.report_path.write_text(content, encoding="utf-8")
    ctx.report_written = True
    return {
        "ok": True,
        "report_path": str(ctx.report_path),
        "bytes_written": len(content.encode("utf-8")),
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a window of lines from a file inside the skill directory "
                "or the run workspace. Use offset+limit for large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path, or relative to the run workspace."},
                    "offset": {"type": "integer", "minimum": 0, "default": 0,
                               "description": "0-based starting line number."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_LIMIT, "default": 400,
                              "description": "Maximum number of lines to return."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory (non-recursive).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Regex search over a file, streamed line-by-line. Returns "
                "matching lines with configurable surrounding context. Use "
                "this to locate crash signatures in large LAVA logs before "
                "reading with read_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "description": "Python regex pattern."},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": MAX_GREP_MATCHES, "default": 50},
                    "context": {"type": "integer", "minimum": 0, "maximum": 20, "default": 2,
                                "description": "Lines of context before and after each match."},
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": (
                "Write the final Markdown triage report. Call this exactly "
                "once when analysis is complete; the loop terminates on "
                "success. The report path is fixed by the runner."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string",
                                "description": "The full Markdown report body."},
                },
                "required": ["content"],
            },
        },
    },
]


_DISPATCH = {
    "read_file": read_file,
    "list_dir": list_dir,
    "grep": grep,
    "write_report": write_report,
}


def dispatch(name: str, args: dict[str, Any], ctx: AgentContext) -> str:
    """Run a tool by name and return the JSON-encoded string result.

    Errors are captured and returned as `{"error": "..."}` strings so the
    model can see them and adjust; they never propagate to the caller.
    """
    fn = _DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})

    try:
        result = fn(**(args or {}), ctx=ctx)
    except ToolError as exc:
        return json.dumps({"error": str(exc)})
    except TypeError as exc:
        return json.dumps({"error": f"bad arguments for {name}: {exc}"})
    except Exception as exc:  # noqa: BLE001 — surface unexpected errors to the model
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return json.dumps(result)
