# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""In-memory run store.

Thread-safe dict of RunRecord keyed by run_id. State is lost on process
restart — this is a known non-goal per the plan.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


RunStatus = Literal["queued", "running", "completed", "failed"]


@dataclass
class RunRecord:
    run_id: str
    job_id: str
    model: str
    node_id: Optional[str] = None
    status: RunStatus = "queued"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    iterations: int = 0
    report_path: Optional[str] = None
    error: Optional[str] = None
    workspace_dir: Optional[str] = None
    log_path: Optional[str] = None


class RunStore:
    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._lock = threading.RLock()

    def create(self, record: RunRecord) -> None:
        with self._lock:
            if record.run_id in self._records:
                raise ValueError(f"run_id {record.run_id} already exists")
            self._records[record.run_id] = record

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._records.get(run_id)

    def update(self, run_id: str, **fields) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            for k, v in fields.items():
                setattr(record, k, v)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values()
                       if r.status in ("queued", "running"))
