# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""KernelCI node lookup, via kci-dev's `KernelCIClient`.

kci-dev is the sanctioned library for talking to KernelCI, so the agent
consumes it instead of reimplementing fetch and auth. Node reads are
unauthenticated GETs against `{api_url}/latest/node/{node_id}`, so no token
and no kci-dev config file are needed — the API URL is passed explicitly.

Everything here is best-effort. `fetch_node` returns None on any failure and
the run continues on the LAVA log alone: the KernelCI picture enriches
triage, it is not a precondition for it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from kcidev import KciDevError, KernelCIClient


log = logging.getLogger(__name__)


def fetch_node(node_id: str, api_url: str) -> Optional[dict[str, Any]]:
    """Fetch one Maestro node. Returns None on any failure.

    kci-dev raises `KciDevError` for HTTP and "not found" cases (it wraps the
    CLI-oriented click exceptions). Anything else is caught too — a triage run
    must not die because KernelCI was briefly unreachable.
    """
    try:
        client = KernelCIClient()
        node = client.get_node(node_id, api_url=api_url)
    except KciDevError as exc:
        log.warning("KernelCI node %s lookup failed: %s", node_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never fatal
        log.warning(
            "KernelCI node %s lookup crashed: %s: %s",
            node_id, type(exc).__name__, exc,
        )
        return None

    if not isinstance(node, dict) or not node:
        log.warning("KernelCI node %s returned no usable payload", node_id)
        return None
    return node


@dataclass(frozen=True)
class NodeContext:
    """The handful of node fields worth putting in front of the model."""

    node_id: str
    kind: Optional[str] = None
    name: Optional[str] = None
    state: Optional[str] = None
    result: Optional[str] = None
    tree: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    describe: Optional[str] = None
    platform: Optional[str] = None
    runtime: Optional[str] = None
    arch: Optional[str] = None
    defconfig: Optional[str] = None
    compiler: Optional[str] = None
    lava_job_id: Optional[str] = None
    lava_log_url: Optional[str] = None
    error_code: Optional[str] = None
    error_msg: Optional[str] = None

    @classmethod
    def from_node(cls, node_id: str, node: dict[str, Any]) -> "NodeContext":
        data = node.get("data") or {}
        rev = data.get("kernel_revision") or {}
        artifacts = node.get("artifacts") or {}
        job_id = data.get("job_id")
        return cls(
            node_id=node.get("id") or node_id,
            kind=node.get("kind"),
            name=node.get("name"),
            state=node.get("state"),
            result=node.get("result"),
            tree=rev.get("tree"),
            branch=rev.get("branch"),
            commit=rev.get("commit"),
            describe=rev.get("describe"),
            platform=data.get("platform"),
            runtime=data.get("runtime"),
            arch=data.get("arch"),
            defconfig=data.get("config_full") or data.get("defconfig"),
            compiler=data.get("compiler"),
            lava_job_id=str(job_id) if job_id else None,
            lava_log_url=artifacts.get("lava_log"),
            error_code=data.get("error_code"),
            error_msg=data.get("error_msg"),
        )

    def summary_lines(self) -> list[str]:
        """Compact `- label : value` lines for the system prompt.

        Deliberately a few scalars, not the whole node — the full JSON is
        written into the run workspace for the agent to read on demand, which
        keeps the fixed prompt prefix cacheable.
        """
        fields = [
            ("KernelCI node id", self.node_id),
            ("Node kind/name", _join(self.kind, self.name)),
            ("Node result", _join(self.state, self.result)),
            ("Tree/branch", _join(self.tree, self.branch, sep="/")),
            ("Commit", self.commit),
            ("Describe", self.describe),
            ("Platform", self.platform),
            ("Runtime (lab)", self.runtime),
            ("Arch/defconfig", _join(self.arch, self.defconfig)),
            ("Compiler", self.compiler),
            ("Node error", _join(self.error_code, self.error_msg)),
        ]
        return [f"- {label:<19}: {value}" for label, value in fields if value]


def _join(*values: Optional[str], sep: str = " ") -> Optional[str]:
    present = [v for v in values if v]
    return sep.join(present) if present else None


def write_node_json(node: dict[str, Any], path: Path) -> Optional[Path]:
    """Persist the raw node into the run workspace for the agent to read.

    The sandboxed `read_file` / `grep` tools already reach anything under the
    run workspace, so this is how the model gets at the full node without
    inflating the prompt. Returns None if the write fails.
    """
    try:
        path.write_text(json.dumps(node, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        log.warning("failed to write node JSON to %s: %s", path, exc)
        return None
    return path
