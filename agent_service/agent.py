# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""LiteLLM tool-calling loop.

Synchronous, blocks the worker thread. Termination conditions (in priority
order):

- `write_report` succeeded → success.
- Assistant returned no tool_calls without writing the report → fail.
- Iteration cap reached → fail.
- Wall-clock deadline exceeded (checked at the top of each iteration) → fail.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import litellm

from .kernelci import NodeContext
from .prompts import build_system_prompt, build_user_message
from .tools import TOOL_SCHEMAS, AgentContext, dispatch


log = logging.getLogger(__name__)


class AgentError(Exception):
    """Raised when the loop cannot produce a report."""


@dataclass
class AgentResult:
    report_path: Path
    iterations: int


@dataclass
class AgentConfig:
    model: str
    max_iterations: int
    run_timeout_sec: int
    model_options: Optional[dict[str, Any]] = None


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Convert a LiteLLM ModelResponse message into a plain dict.

    LiteLLM returns pydantic-like objects; we extract just the fields the
    Chat Completions API expects on the next turn.
    """
    out: dict[str, Any] = {"role": "assistant"}
    content = getattr(message, "content", None)
    if content is not None:
        out["content"] = content
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out


def _parse_tool_args(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(f"model returned invalid tool arguments: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AgentError("tool arguments must be a JSON object")
    return parsed


def run_agent(
    *,
    job_id: str,
    log_path: Path,
    log_size_bytes: int,
    log_line_count: int,
    log_truncated: bool,
    report_path: Path,
    skill_dir: Path,
    run_workspace: Path,
    config: AgentConfig,
    node_context: Optional[NodeContext] = None,
    node_json_path: Optional[Path] = None,
    on_iteration: Optional[Callable[[int, dict[str, Any]], None]] = None,
) -> AgentResult:
    ctx = AgentContext(
        skill_dir=skill_dir,
        run_workspace=run_workspace,
        report_path=report_path,
    )

    system_prompt = build_system_prompt(
        skill_dir=skill_dir,
        log_path=log_path,
        log_size_bytes=log_size_bytes,
        log_line_count=log_line_count,
        log_truncated=log_truncated,
        report_path=report_path,
        job_id=job_id,
        node_context=node_context,
        node_json_path=node_json_path,
    )
    user_msg = build_user_message(
        job_id=job_id, log_path=log_path, report_path=report_path,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    deadline = time.monotonic() + config.run_timeout_sec
    extra_kwargs = dict(config.model_options or {})

    for iteration in range(1, config.max_iterations + 1):
        if time.monotonic() > deadline:
            raise AgentError(
                f"run exceeded run_timeout_sec={config.run_timeout_sec}"
            )

        log.info("agent iter=%d model=%s", iteration, config.model)
        try:
            response = litellm.completion(
                model=config.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                **extra_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — provider errors are opaque
            raise AgentError(
                f"model provider error: {type(exc).__name__}: {exc}"
            ) from exc

        message = response.choices[0].message
        messages.append(_message_to_dict(message))

        tool_calls = getattr(message, "tool_calls", None) or []
        if on_iteration is not None:
            on_iteration(iteration, {
                "tool_calls": [tc.function.name for tc in tool_calls],
                "content_len": len(message.content or ""),
            })

        if not tool_calls:
            if ctx.report_written:
                return AgentResult(report_path=report_path, iterations=iteration)
            raise AgentError(
                "agent terminated without writing a report "
                "(assistant produced text but no tool_calls)"
            )

        for tc in tool_calls:
            args = _parse_tool_args(tc.function.arguments)
            result_str = dispatch(tc.function.name, args, ctx)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

        if ctx.report_written:
            return AgentResult(report_path=report_path, iterations=iteration)

    raise AgentError(
        f"iteration cap ({config.max_iterations}) reached without report"
    )
