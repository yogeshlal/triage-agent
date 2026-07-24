# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""FastAPI app factory and uvicorn entrypoint.

Run with: `python -m agent_service.main`

Loads `.env` at startup into `os.environ` so LiteLLM, the Anthropic/OpenAI
SDK, and LavaClient all see the same values that `pydantic-settings` reads
into the typed `Settings` object.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from .api import router
from .config import PROJECT_ROOT, Settings, get_settings
from .store import RunStore


log = logging.getLogger(__name__)


def _load_env_file() -> None:
    """Populate os.environ from `.env` at the project root.

    pydantic-settings reads `.env` only for its own typed fields; provider
    variables (`OPENAI_API_KEY`, `OPENAI_API_BASE`, `ANTHROPIC_API_KEY`, …)
    must be in `os.environ` for LiteLLM / the SDKs to pick them up.
    `override=False` means variables already set in the real environment win
    over the file.
    """
    env_path: Path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
        log.info("loaded env from %s", env_path)
    else:
        log.info("no .env found at %s (relying on process environment)", env_path)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _configure_llm_tls(settings: Settings) -> None:
    """Apply corporate-CA / verification settings to the outbound HTTPS stack.

    Must run BEFORE any litellm/anthropic-sdk call so the httpx client picks
    up the trust store from env vars at construction time. Setting both
    SSL_CERT_FILE and REQUESTS_CA_BUNDLE covers both httpx (LiteLLM,
    anthropic-sdk) and requests (LavaClient).
    """
    if settings.llm_ca_bundle is not None:
        bundle = str(settings.llm_ca_bundle)
        if not settings.llm_ca_bundle.is_file():
            log.warning("TRIAGE_LLM_CA_BUNDLE=%s does not exist; ignoring", bundle)
        else:
            os.environ.setdefault("SSL_CERT_FILE", bundle)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
            log.info("using CA bundle %s for outbound TLS", bundle)

    if not settings.llm_ssl_verify:
        try:
            import litellm  # noqa: WPS433 — deliberate late import
            litellm.ssl_verify = False
        except Exception as exc:  # noqa: BLE001
            log.warning("could not disable litellm ssl_verify: %s", exc)
        log.warning(
            "TRIAGE_LLM_SSL_VERIFY=false — outbound TLS certificate "
            "verification is DISABLED. Use only as a last resort."
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    Callers are expected to have loaded `.env` into the process environment
    (via `_load_env_file()` or the shell) before invoking this. When run
    via `python -m agent_service.main`, `main()` handles that. When run
    via `uvicorn agent_service.main:create_app` directly, load `.env` in
    the caller or export the vars in the shell.
    """
    settings = settings or get_settings()
    _configure_llm_tls(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        app.state.settings = settings
        app.state.store = RunStore()
        app.state.executor = ThreadPoolExecutor(
            max_workers=settings.max_workers,
            thread_name_prefix="triage-worker",
        )
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="LAVA Triage Agent",
        description="Triage LAVA job failures with a configurable LLM.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


def main() -> None:
    _configure_logging()
    _load_env_file()
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
