# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""FastAPI routes.

- `POST /{job_id}`       — submit a triage run for the LAVA job. Optional
                            body allows a KernelCI `node_id` plus per-request
                            `model` / `model_options` overrides. Returns
                            `run_id` and status "queued".
- `GET  /triage/{run_id}`— poll status; when completed, the report is inlined.
- `GET  /health`         — liveness + config snapshot.

Concurrency model: a ThreadPoolExecutor lives on `app.state.executor`.
Handlers submit runs and return immediately; workers update `RunStore` as
they progress.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Request, status

from .runner import execute_run
from .schemas import (
    HealthResponse,
    TriageOptions,
    TriageQueuedResponse,
    TriageStatusResponse,
)
from .store import RunRecord, RunStore


log = logging.getLogger(__name__)

router = APIRouter()


# Registered before the catch-all `POST /{job_id}` so `/health` is not treated
# as a job id. Different HTTP methods make GET /health unambiguous, but a
# hypothetical POST /health would still route to submit_triage — we prevent
# that below by reserving a small set of names.
_RESERVED_JOB_IDS = {"health", "triage", "docs", "openapi.json", "redoc"}


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    store: RunStore = request.app.state.store
    return HealthResponse(
        status="ok",
        default_model=settings.default_model,
        skill_dir=str(settings.skill_dir),
        active_runs=store.active_count(),
    )


@router.get("/triage/{run_id}", response_model=TriageStatusResponse)
def get_triage(run_id: str, request: Request) -> TriageStatusResponse:
    store: RunStore = request.app.state.store
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")

    report: str | None = None
    if record.status == "completed" and record.report_path:
        try:
            report = Path(record.report_path).read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("failed to read report %s: %s", record.report_path, exc)

    return TriageStatusResponse(
        run_id=record.run_id,
        job_id=record.job_id,
        node_id=record.node_id,
        model=record.model,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        iterations=record.iterations,
        report_path=record.report_path,
        report=report,
        error=record.error,
    )


@router.post(
    "/{job_id}",
    response_model=TriageQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_triage(
    job_id: str,
    request: Request,
    options: Optional[TriageOptions] = Body(default=None),
) -> TriageQueuedResponse:
    if job_id in _RESERVED_JOB_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"'{job_id}' is a reserved path, not a valid job id",
        )

    settings = request.app.state.settings
    store: RunStore = request.app.state.store
    executor: ThreadPoolExecutor = request.app.state.executor

    # Cap in-flight work so a runaway caller cannot starve the pool.
    active = store.active_count()
    if active >= settings.max_active_runs:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"too many active runs ({active}/{settings.max_active_runs}); "
                f"retry later"
            ),
        )

    opts = options or TriageOptions()
    run_id = uuid4().hex[:12]
    chosen_model = opts.model or settings.default_model

    record = RunRecord(
        run_id=run_id,
        job_id=job_id,
        model=chosen_model,
        node_id=opts.node_id,
    )
    store.create(record)

    executor.submit(
        execute_run,
        run_id=run_id,
        job_id=job_id,
        node_id=opts.node_id,
        model=chosen_model,
        model_options=opts.model_options,
        log_url=opts.log_url,
        settings=settings,
        store=store,
    )

    log.info(
        "queued run %s for lava job %s (node_id=%s, model=%s)",
        run_id, job_id, opts.node_id or "-", chosen_model,
    )
    return TriageQueuedResponse(run_id=run_id, status="queued")
