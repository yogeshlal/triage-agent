# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Runtime configuration loaded from environment variables.

All service-level knobs are here. LAVA and provider credentials are read by
their respective client libraries (LavaClient, LiteLLM) directly from the
environment, so they are not duplicated in Settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_model: str = Field(
        default="anthropic/claude-4-5-sonnet",
        alias="TRIAGE_MODEL_DEFAULT",
    )
    skill_dir: Path = Field(
        default=PROJECT_ROOT / "skills" / "kernel-triage",
        alias="TRIAGE_SKILL_DIR",
    )
    workspace_root: Path = Field(
        default=PROJECT_ROOT / "runs",
        alias="TRIAGE_WORKSPACE_ROOT",
    )
    report_dir: Path = Field(
        default=PROJECT_ROOT / "reports",
        alias="TRIAGE_REPORT_DIR",
    )
    max_iterations: int = Field(default=40, alias="TRIAGE_MAX_ITERATIONS")
    run_timeout_sec: int = Field(default=1800, alias="TRIAGE_RUN_TIMEOUT_SEC")
    max_workers: int = Field(default=1, alias="TRIAGE_MAX_WORKERS")
    max_active_runs: int = Field(default=16, alias="TRIAGE_MAX_ACTIVE_RUNS")
    lava_log_max_bytes: int = Field(
        default=100 * 1024 * 1024,
        alias="TRIAGE_LAVA_LOG_MAX_BYTES",
    )
    host: str = Field(default="0.0.0.0", alias="TRIAGE_HOST")
    port: int = Field(default=8080, alias="TRIAGE_PORT")

    # KernelCI (Maestro) API, reached through kci-dev's KernelCIClient. Node
    # reads are unauthenticated, so no token is needed here. Used only when a
    # caller supplies `node_id`.
    kci_api_url: str = Field(
        default="https://api.kernelci.org/",
        alias="TRIAGE_KCI_API_URL",
    )
    # Web dashboard base, for human-facing node links in the report. Optional.
    kci_dashboard_url: Optional[str] = Field(
        default=None,
        alias="TRIAGE_KERNELCI_WEB_URL",
    )

    # Outbound TLS to the LLM provider. Corporate MITM proxies typically need
    # `llm_ca_bundle` pointed at the company CA PEM; `llm_ssl_verify=false` is
    # an escape hatch and disables verification globally for LiteLLM.
    llm_ca_bundle: Optional[Path] = Field(default=None, alias="TRIAGE_LLM_CA_BUNDLE")
    llm_ssl_verify: bool = Field(default=True, alias="TRIAGE_LLM_SSL_VERIFY")


def get_settings() -> Settings:
    return Settings()
