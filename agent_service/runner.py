# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Runner: turns a queued run into a completed report.

Executed on a worker thread from ThreadPoolExecutor. Owns:

- KernelCI node lookup (via kci-dev), when the caller supplied a `node_id`.
- LAVA log fetch, resolved in three tiers: an explicit `log_url`, then the
  node's `artifacts.lava_log`, then the LAVA REST API (via LavaClient).
- Workspace preparation.
- Agent invocation.
- Store status transitions.
- Failure classification per the plan's failure table.
"""

from __future__ import annotations

import gzip
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .agent import AgentConfig, AgentError, run_agent
from .config import PROJECT_ROOT, Settings
from .delivery import ReportContext, deliver_report
from .kernelci import NodeContext, fetch_node, write_node_json
from .store import RunStore


# The sibling `lava_helper_script/` directory is not a package — inject it on
# sys.path so we can `import lava_client`.
_LAVA_DIR = PROJECT_ROOT / "lava_helper_script"
if str(_LAVA_DIR) not in sys.path:
    sys.path.insert(0, str(_LAVA_DIR))

from lava_client import LavaAPIError, LavaAuthError, LavaClient  # noqa: E402


log = logging.getLogger(__name__)

TRUNCATION_MARKER = "\n\n# --- log truncated by triage agent ---\n"
_GZIP_MAGIC = b"\x1f\x8b"
_URL_FETCH_TIMEOUT = (10, 300)  # (connect, read) seconds


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _combine_fetch_error(url_error: Optional[str], lava_error: str) -> str:
    """When URL fetch was attempted and failed, surface both errors so
    operators don't have to guess which path was tried."""
    if url_error:
        return f"log_url fetch failed ({url_error}); {lava_error}"
    return lava_error


def _prepare_workspace(settings: Settings, run_id: str) -> Path:
    workspace = settings.workspace_root / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _write_log(text: str, log_path: Path, max_bytes: int) -> tuple[Path, int, int, bool]:
    """Truncate at byte boundary → last newline → sentinel marker; write; stat."""
    if not text:
        raise RuntimeError(f"log text is empty for {log_path.name}")

    truncated = False
    if len(text.encode("utf-8")) > max_bytes:
        encoded = text.encode("utf-8")[:max_bytes]
        text = encoded.decode("utf-8", errors="ignore")
        text = text.rsplit("\n", 1)[0] + TRUNCATION_MARKER
        truncated = True

    log_path.write_text(text, encoding="utf-8")
    size = log_path.stat().st_size
    line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
    return log_path, size, line_count, truncated


def _fetch_log(job_id: str, workspace: Path, max_bytes: int) -> tuple[Path, int, int, bool]:
    with LavaClient() as client:
        text = client.get_job_logs_plain(job_id)

    if not text:
        raise RuntimeError(f"lava log empty for job {job_id}")

    log_path = workspace / f"lava_job_{job_id}.log"
    return _write_log(text, log_path, max_bytes)


def _fetch_log_from_url(log_url: str, job_id: str, workspace: Path,
                        max_bytes: int) -> tuple[Path, int, int, bool]:
    """Download the log from KernelCI storage (or any HTTP endpoint) and
    write it to the workspace. Gunzip'd on the fly when the payload has the
    gzip magic bytes or the URL path ends with `.gz`."""
    resp = requests.get(log_url, timeout=_URL_FETCH_TIMEOUT)
    resp.raise_for_status()
    raw = resp.content

    path_lc = urlparse(log_url).path.lower()
    if raw[:2] == _GZIP_MAGIC or path_lc.endswith(".gz"):
        raw = gzip.decompress(raw)

    text = raw.decode("utf-8", errors="replace")
    if not text:
        raise RuntimeError(f"log at {log_url} is empty")

    log_path = workspace / f"lava_job_{job_id}.log"
    return _write_log(text, log_path, max_bytes)


def execute_run(
    *,
    run_id: str,
    job_id: str,
    model: str,
    model_options: dict | None,
    node_id: Optional[str] = None,
    log_url: Optional[str] = None,
    settings: Settings,
    store: RunStore,
) -> None:
    """Entrypoint for the worker thread. Never raises — every failure is
    captured into the store.

    ``node_id`` is the KernelCI (Maestro) node for this job, when the caller
    knows it. The node is fetched through kci-dev and dumped into the run
    workspace so the agent can read it, and a few of its scalars go into the
    system prompt. A failed lookup is non-fatal: triage falls back to the
    LAVA log alone.

    The log is resolved in three tiers — an explicit ``log_url``, then the
    node's ``artifacts.lava_log``, then the LAVA REST API. The URL tiers are
    preferred because they hit the sanitized artifact already in KernelCI
    storage and avoid a second LAVA fetch."""
    store.update(run_id, status="running", started_at=_now())

    # Declared before `fail()` so the delivery closure can read them on the
    # early-failure paths, where the node lookup has not happened yet.
    node_ctx: Optional[NodeContext] = None
    node_json_path: Optional[Path] = None

    def deliver(status: str, error: Optional[str], report: Optional[str]) -> None:
        """Best-effort report delivery. Never affects the run's outcome."""
        try:
            ctx = ReportContext.build(
                run_id=run_id,
                job_id=job_id,
                node_id=node_id,
                node_context=node_ctx,
                status=status,
                error=error,
                report=report,
                settings=settings,
            )
            outcomes = deliver_report(ctx, settings)
            if outcomes:
                log.info("run %s delivery: %s", run_id, outcomes)
        except Exception:  # noqa: BLE001 — delivery is never fatal
            log.exception("run %s delivery crashed", run_id)

    def fail(message: str) -> None:
        log.warning("run %s failed: %s", run_id, message)
        store.update(run_id, status="failed", finished_at=_now(), error=message)
        deliver("failed", message, None)

    try:
        workspace = _prepare_workspace(settings, run_id)
        store.update(run_id, workspace_dir=str(workspace))
    except OSError as exc:
        fail(f"workspace setup failed: {exc}")
        return

    # KernelCI-side context. Best-effort: a lookup failure only costs us the
    # enrichment, never the run.
    if node_id:
        node: Optional[dict[str, Any]] = fetch_node(node_id, settings.kci_api_url)
        if node is not None:
            node_ctx = NodeContext.from_node(node_id, node)
            node_json_path = write_node_json(
                node, workspace / f"kernelci_node_{node_id}.json",
            )
            log.info(
                "run %s node %s: %s/%s @ %s on %s (%s)",
                run_id, node_id, node_ctx.tree, node_ctx.branch,
                (node_ctx.commit or "?")[:12], node_ctx.platform,
                node_ctx.runtime,
            )

    # Tier 2: the log URL recorded on the node, when the caller didn't pass one.
    if not log_url and node_ctx is not None and node_ctx.lava_log_url:
        log_url = node_ctx.lava_log_url
        log.info("run %s using log URL from node %s", run_id, node_id)

    log_path, log_size, log_lines, truncated = None, None, None, None  # type: ignore[assignment]
    url_error: Optional[str] = None

    if log_url:
        try:
            log_path, log_size, log_lines, truncated = _fetch_log_from_url(
                log_url, job_id, workspace, settings.lava_log_max_bytes,
            )
            log.info("run %s fetched log from URL (%d bytes)", run_id, log_size)
        except (requests.RequestException, OSError, ValueError, RuntimeError) as exc:
            url_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "run %s URL fetch failed (%s); falling back to LAVA job %s",
                run_id, url_error, job_id,
            )
        except Exception as exc:  # noqa: BLE001 — gzip / decode surprises
            url_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "run %s URL fetch crashed (%s); falling back to LAVA job %s",
                run_id, url_error, job_id,
            )

    if log_path is None:
        try:
            log_path, log_size, log_lines, truncated = _fetch_log(
                job_id, workspace, settings.lava_log_max_bytes,
            )
        except LavaAuthError as exc:
            fail(_combine_fetch_error(url_error, f"lava auth failed: {exc}"))
            return
        except LavaAPIError as exc:
            status = exc.status_code if exc.status_code is not None else "?"
            fail(_combine_fetch_error(url_error,
                                      f"lava fetch failed: HTTP {status} — {exc}"))
            return
        except RuntimeError as exc:
            fail(_combine_fetch_error(url_error, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected lava fetch error")
            fail(_combine_fetch_error(
                url_error,
                f"unexpected lava fetch error: {type(exc).__name__}: {exc}",
            ))
            return

    store.update(run_id, log_path=str(log_path))

    report_path = settings.report_dir / f"{run_id}_report.md"

    def on_iteration(n: int, meta: dict) -> None:
        store.update(run_id, iterations=n)
        log.info("run %s iter=%d tools=%s", run_id, n, meta.get("tool_calls"))

    agent_config = AgentConfig(
        model=model,
        max_iterations=settings.max_iterations,
        run_timeout_sec=settings.run_timeout_sec,
        model_options=model_options,
    )

    try:
        result = run_agent(
            job_id=job_id,
            log_path=log_path,
            log_size_bytes=log_size,
            log_line_count=log_lines,
            log_truncated=truncated,
            report_path=report_path,
            skill_dir=settings.skill_dir,
            run_workspace=workspace,
            node_context=node_ctx,
            node_json_path=node_json_path,
            config=agent_config,
            on_iteration=on_iteration,
        )
    except AgentError as exc:
        fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — belt-and-braces
        log.error("run %s internal error\n%s", run_id, traceback.format_exc())
        fail(f"internal error: {type(exc).__name__}: {exc}")
        return

    if not result.report_path.exists() or result.report_path.stat().st_size == 0:
        fail("agent wrote empty report")
        return

    store.update(
        run_id,
        status="completed",
        finished_at=_now(),
        iterations=result.iterations,
        report_path=str(result.report_path),
    )
    log.info("run %s completed in %d iterations", run_id, result.iterations)

    try:
        report_text = result.report_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("run %s could not read report for delivery: %s", run_id, exc)
        report_text = None
    deliver("completed", None, report_text)
