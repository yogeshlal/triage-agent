# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""Report delivery: GitHub issue and email.

Moved here from the pipeline's `triage_notifier`, which used to poll this
service for ~35 minutes and then deliver the result. Delivery belongs to
whoever owns the report, and that is this service — the LAVA callback is now
only a trigger and returns as soon as the run is queued.

Both channels are optional and independently gated: GitHub needs
`TRIAGE_GITHUB_REPO` + `TRIAGE_GITHUB_TOKEN`, email needs `TRIAGE_SMTP_HOST`
+ `TRIAGE_SMTP_FROM` + `TRIAGE_EMAIL_TO`. Nothing here may raise into the
runner: `deliver_report` catches everything and reports outcomes instead.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

import requests

from .config import Settings
from .kernelci import NodeContext
from .prompts import lava_job_url


log = logging.getLogger(__name__)

_DEFAULT_LABELS = ["triage", "lava-failure"]
_HTTP_TIMEOUT = 30


def _split_csv(raw: Optional[str]) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


@dataclass(frozen=True)
class ReportContext:
    """Everything the two delivery channels need to render a message."""

    run_id: str
    lava_job_id: str
    status: str                      # completed | failed
    node_id: Optional[str] = None
    error: Optional[str] = None
    report: Optional[str] = None
    kernel_tree: Optional[str] = None
    kernel_branch: Optional[str] = None
    kernel_commit: Optional[str] = None
    device_type: Optional[str] = None
    lava_log_url: Optional[str] = None
    kernelci_web_url: Optional[str] = None
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        job_id: str,
        node_id: Optional[str],
        node_context: Optional[NodeContext],
        status: str,
        error: Optional[str],
        report: Optional[str],
        settings: Settings,
    ) -> "ReportContext":
        nc = node_context
        return cls(
            run_id=run_id,
            lava_job_id=str(job_id),
            status=status,
            node_id=node_id or (nc.node_id if nc else None),
            error=error,
            report=report,
            kernel_tree=nc.tree if nc else None,
            kernel_branch=nc.branch if nc else None,
            kernel_commit=nc.commit if nc else None,
            device_type=nc.platform if nc else None,
            lava_log_url=nc.lava_log_url if nc else None,
            kernelci_web_url=settings.kci_dashboard_url,
        )


# --------------------------------------------------------------------------
# Formatting shared between GitHub and email
# --------------------------------------------------------------------------

def _format_subject(ctx: ReportContext) -> str:
    tree = ctx.kernel_tree or "unknown-tree"
    branch = ctx.kernel_branch or "unknown-branch"
    commit = (ctx.kernel_commit or "")[:12] or "unknown"
    device = ctx.device_type or "unknown-device"
    return (f"[triage] {tree}/{branch}@{commit} — "
            f"LAVA job {ctx.lava_job_id} on {device}")


def _format_report_body(ctx: ReportContext) -> str:
    # The node_id MUST appear in the body: GitHub de-duplication searches
    # `in:body <node_id>` to decide comment-vs-new-issue. Dropping it turns
    # every retrigger into a fresh issue.
    lines = [
        "| Field | Value |",
        "|---|---|",
        f"| node_id | `{ctx.node_id or '-'}` |",
        f"| LAVA job | `{ctx.lava_job_id}` |",
        f"| tree/branch | `{ctx.kernel_tree or '?'}` / `{ctx.kernel_branch or '?'}` |",
        f"| commit | `{ctx.kernel_commit or '?'}` |",
        f"| device | `{ctx.device_type or '?'}` |",
        f"| triage status | `{ctx.status}` |",
        f"| triage run_id | `{ctx.run_id}` |",
        f"| generated | {ctx.generated_at.isoformat(timespec='seconds')} |",
    ]
    job_url = lava_job_url(ctx.lava_job_id)
    if job_url:
        lines.append(f"| LAVA job URL | {job_url} |")
    if ctx.lava_log_url:
        lines.append(f"| LAVA log | {ctx.lava_log_url} |")
    if ctx.kernelci_web_url and ctx.node_id:
        lines.append(
            f"| KernelCI node | {ctx.kernelci_web_url}/node/{ctx.node_id} |"
        )

    body = ["## Triage report", "", *lines, ""]
    if ctx.status == "completed" and ctx.report:
        body.extend(["## Analysis", "", ctx.report])
    else:
        body.extend([
            "## Analysis unavailable",
            "",
            f"Triage did not produce a report: **{ctx.status}**.",
            f"Error: `{ctx.error or 'no detail'}`",
        ])
    return "\n".join(body)


def _render_html(markdown_text: str) -> Optional[str]:
    try:
        import markdown  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return markdown.markdown(
            markdown_text,
            extensions=["fenced_code", "tables"],
        )
    except Exception as exc:  # noqa: BLE001 — optional dep, don't crash delivery
        log.warning("markdown render failed: %s", exc)
        return None


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

class GithubDelivery:
    _API = "https://api.github.com"

    def __init__(self, repo: str, token: str, labels: list[str],
                 session: Optional[requests.Session] = None) -> None:
        self._repo = repo
        self._session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._labels = labels

    def deliver(self, ctx: ReportContext) -> str:
        """Returns 'created', 'commented', or 'failed'."""
        body = _format_report_body(ctx)
        title = _format_subject(ctx)

        # Without a node_id there is no dedup key, so always open a new issue.
        existing = self._find_open_issue(ctx.node_id) if ctx.node_id else None
        if existing is not None:
            ok = self._post_comment(existing, body)
            return "commented" if ok else "failed"
        ok = self._create_issue(title, body)
        return "created" if ok else "failed"

    def _find_open_issue(self, node_id: str) -> Optional[int]:
        q = f"repo:{self._repo} is:issue is:open in:body {node_id}"
        try:
            resp = self._request("GET", f"{self._API}/search/issues",
                                 params={"q": q})
        except requests.RequestException as exc:
            log.warning("github search failed for node_id=%s: %s", node_id, exc)
            return None
        if resp.status_code >= 400:
            log.warning("github search %s: %s", resp.status_code, resp.text[:200])
            return None
        items = resp.json().get("items", [])
        return items[0]["number"] if items else None

    def _create_issue(self, title: str, body: str) -> bool:
        payload = {"title": title, "body": body, "labels": self._labels}
        try:
            resp = self._request(
                "POST", f"{self._API}/repos/{self._repo}/issues", json=payload,
            )
        except requests.RequestException as exc:
            log.warning("github create issue error: %s", exc)
            return False
        if resp.status_code >= 400:
            log.warning("github create issue %s: %s",
                        resp.status_code, resp.text[:200])
            return False
        log.info("github issue created: %s", resp.json().get("html_url"))
        return True

    def _post_comment(self, issue_number: int, body: str) -> bool:
        payload = {"body": body}
        try:
            resp = self._request(
                "POST",
                f"{self._API}/repos/{self._repo}/issues/{issue_number}/comments",
                json=payload,
            )
        except requests.RequestException as exc:
            log.warning("github comment error on #%d: %s", issue_number, exc)
            return False
        if resp.status_code >= 400:
            log.warning("github comment #%d %s: %s",
                        issue_number, resp.status_code, resp.text[:200])
            return False
        log.info("github comment posted on #%d", issue_number)
        return True

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Single retry on 5xx/429 with 5s sleep."""
        for attempt in (1, 2):
            resp = self._session.request(
                method, url, headers=self._headers,
                timeout=_HTTP_TIMEOUT, **kwargs,
            )
            if attempt == 1 and (resp.status_code >= 500
                                 or resp.status_code == 429):
                log.info("github %s %s → %d; retrying in 5s",
                         method, url, resp.status_code)
                time.sleep(5)
                continue
            return resp
        return resp  # type: ignore[return-value] — unreachable in practice


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

class EmailDelivery:
    def __init__(self, host: str, port: int, user: Optional[str],
                 password: Optional[str], sender: str,
                 to: list[str], cc: list[str]) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from = sender
        self._to = to
        self._cc = cc

    def deliver(self, ctx: ReportContext) -> bool:
        msg = EmailMessage()
        msg["Subject"] = _format_subject(ctx)
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        if self._cc:
            msg["Cc"] = ", ".join(self._cc)

        plain = _format_report_body(ctx)
        msg.set_content(plain)

        html = _render_html(plain)
        if html is not None:
            msg.add_alternative(html, subtype="html")

        try:
            if self._port == 465:
                tls = ssl.create_default_context()
                with smtplib.SMTP_SSL(self._host, self._port, context=tls,
                                      timeout=_HTTP_TIMEOUT) as smtp:
                    self._maybe_login(smtp)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(self._host, self._port,
                                  timeout=_HTTP_TIMEOUT) as smtp:
                    smtp.ehlo()
                    if self._port == 587 or smtp.has_extn("starttls"):
                        smtp.starttls(context=ssl.create_default_context())
                        smtp.ehlo()
                    self._maybe_login(smtp)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("smtp send failed: %s", exc)
            return False
        log.info("triage email sent to %s", msg["To"])
        return True

    def _maybe_login(self, smtp: smtplib.SMTP) -> None:
        if self._user and self._password:
            smtp.login(self._user, self._password)


# --------------------------------------------------------------------------
# Entrypoint used by the runner
# --------------------------------------------------------------------------

def build_github(settings: Settings) -> Optional[GithubDelivery]:
    if not (settings.github_repo and settings.github_token):
        return None
    return GithubDelivery(
        settings.github_repo,
        settings.github_token,
        _split_csv(settings.github_labels) or list(_DEFAULT_LABELS),
    )


def build_email(settings: Settings) -> Optional[EmailDelivery]:
    recipients = _split_csv(settings.email_to)
    if not (settings.smtp_host and settings.smtp_from and recipients):
        return None
    return EmailDelivery(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_user,
        settings.smtp_password,
        settings.smtp_from,
        recipients,
        _split_csv(settings.email_cc),
    )


def deliver_report(ctx: ReportContext, settings: Settings) -> dict[str, str]:
    """Deliver on every configured channel. Never raises.

    Returns a channel → outcome map for logging, e.g.
    ``{"github": "created", "email": "sent"}``.
    """
    outcomes: dict[str, str] = {}

    github = build_github(settings)
    if github is not None:
        try:
            outcomes["github"] = github.deliver(ctx)
        except Exception:  # noqa: BLE001 — delivery must not affect the run
            log.exception("github delivery crashed")
            outcomes["github"] = "failed"

    email = build_email(settings)
    if email is not None:
        try:
            outcomes["email"] = "sent" if email.deliver(ctx) else "failed"
        except Exception:  # noqa: BLE001
            log.exception("email delivery crashed")
            outcomes["email"] = "failed"

    return outcomes
