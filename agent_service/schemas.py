# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Pydantic request/response schemas for the HTTP layer.

Field naming avoids pydantic v2's protected `model_` namespace: use `model`
(bare, allowed) and `model_options` (renamed from `model_config` which
collides with BaseModel.model_config).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TriageOptions(BaseModel):
    """Optional body for `POST /{job_id}`. Both fields default to server config."""

    model: Optional[str] = Field(default=None, description="LiteLLM model string override")
    model_options: Optional[dict[str, Any]] = Field(
        default=None,
        description="Extra kwargs merged into litellm.completion (api_base, temperature, max_tokens, ...)",
    )
    log_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional HTTP(S) URL to a plain or gzipped LAVA log. When set, "
            "the runner downloads from this URL first and falls back to the "
            "LAVA REST API on any failure. Callers that already have the log "
            "in KernelCI storage should pass its URL here to avoid a second "
            "LAVA fetch."
        ),
    )


class TriageQueuedResponse(BaseModel):
    run_id: str
    status: Literal["queued"]


class TriageStatusResponse(BaseModel):
    run_id: str
    job_id: str
    model: str
    status: Literal["queued", "running", "completed", "failed"]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    iterations: int = 0
    report_path: Optional[str] = None
    report: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    default_model: str
    skill_dir: str
    active_runs: int
