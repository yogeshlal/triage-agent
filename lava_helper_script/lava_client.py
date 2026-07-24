# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2026 Qualcomm Technologies, Inc. and/or its subsidiaries.
# Author: Yogesh Lal <yogesh.lal@oss.qualcomm.com>

"""LAVA REST v0.2 client library.

Exports a `LavaClient` class that wraps the endpoints under
`{lava_url}/api/v0.2/`. Constructor verifies access by calling
`/system/whoami/` — a failed check raises `LavaAuthError` immediately so
callers never operate against an unreachable or unauthorised server.

Environment fallback: if `url` or `token` are omitted, the constructor
reads `LAVA_URL` and `LAVA_TOKEN` respectively.

Automatic retries: 3 attempts on 502/503/504 for GET/HEAD only, with
exponential backoff (0.5s, 1s, 2s). POST is never retried, so job
submissions and cancels can never be duplicated by the retry layer.
Disable with `LavaClient(..., retries=0)`.

The client is a context manager and closes its HTTP session on exit:
    with LavaClient() as c:
        c.get_device_types()

Public methods:
    submit_job(definition, *, validate=True, strict=False) -> list[int|str]
    get_job_status(job_id) -> dict            # full serialized TestJob
    get_job_result(job_id, fmt="json", **params) -> list[dict] | bytes
    get_job_logs(job_id, start=0, end=None) -> str
    get_job_logs_plain(job_id) -> str
    cancel_job(job_id) -> dict                # GET (server accepts GET only)
    list_jobs(*, state=None, health=None, device_type=None,
              submitter=None, limit=None) -> list[dict]
    get_device_types() -> list[dict]
"""

from __future__ import annotations

import os
from typing import Any, Iterator
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_TIMEOUT = (10, 120)  # (connect, read) seconds
DEFAULT_RETRIES = 3

# Server-side TestJob.STATE_REVERSE / HEALTH_REVERSE use Title-Case keys, so
# these maps carry both the case-insensitive input vocabulary (upper) and
# the exact wire format the server expects (Title-Case value).
_JOB_STATES = {
    "SUBMITTED":  "Submitted",
    "SCHEDULING": "Scheduling",
    "SCHEDULED":  "Scheduled",
    "RUNNING":    "Running",
    "CANCELING":  "Canceling",
    "FINISHED":   "Finished",
}
_JOB_HEALTHS = {
    "UNKNOWN":    "Unknown",
    "COMPLETE":   "Complete",
    "INCOMPLETE": "Incomplete",
    "CANCELED":   "Canceled",
}

# Ordered tuples — the count matrix in get_device_types() iterates these in a
# fixed order, so preserve tuple form (don't "clean up" to sets).
_DEVICE_STATES = ("Idle", "Reserved", "Running")
_DEVICE_HEALTHS = ("Good", "Unknown", "Looping", "Bad", "Maintenance",
                   "Retired")


class LavaError(Exception):
    """Base class for LAVA client errors."""


class LavaAuthError(LavaError):
    """Raised when the server rejects the token or is unreachable at init."""


class LavaAPIError(LavaError):
    """Raised on any non-2xx REST response after auth has succeeded."""

    def __init__(self, message: str, status_code: int | None = None,
                 body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class LavaClient:
    def __init__(self, url: str | None = None, token: str | None = None,
                 *, timeout: tuple[float, float] | float = DEFAULT_TIMEOUT,
                 verify: bool | str = True,
                 retries: int = DEFAULT_RETRIES) -> None:
        url = url or os.environ.get("LAVA_URL")
        token = token or os.environ.get("LAVA_TOKEN")
        if not url:
            raise LavaAuthError("LAVA URL not provided (arg or LAVA_URL env)")
        if not token:
            raise LavaAuthError("LAVA token not provided (arg or LAVA_TOKEN env)")

        self.base_url = url.rstrip("/") + "/"
        self.api_root = urljoin(self.base_url, "api/v0.2/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        })
        self._session.verify = verify

        if retries > 0:
            # Retry only idempotent methods so POST /jobs/ can never
            # double-submit and POST /jobs/{id}/cancel/ can never
            # double-signal. raise_on_status=False lets _request surface
            # the final response through our normal error path.
            retry = Retry(
                total=retries,
                status_forcelist=(502, 503, 504),
                allowed_methods=frozenset(("GET", "HEAD")),
                backoff_factor=0.5,
                raise_on_status=False,
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)

        self.whoami = self._verify_access()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "LavaClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ---- internals -------------------------------------------------------

    def _url(self, path: str) -> str:
        return urljoin(self.api_root, path.lstrip("/"))

    def _verify_access(self) -> str:
        try:
            resp = self._session.get(self._url("system/whoami/"),
                                     timeout=self.timeout)
        except requests.RequestException as exc:
            raise LavaAuthError(f"cannot reach {self.api_root}: {exc}") from exc
        if resp.status_code == 401:
            raise LavaAuthError("token rejected by server (401)")
        if resp.status_code == 403:
            raise LavaAuthError("token authenticated but access forbidden (403)")
        if not resp.ok:
            raise LavaAuthError(
                f"access check failed: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            return resp.json().get("user", "")
        except ValueError:
            return ""

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = self._session.request(method, self._url(path), **kwargs)
        except requests.RequestException as exc:
            raise LavaAPIError(f"{method} {path} failed: {exc}") from exc
        if not resp.ok:
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text[:500]
            raise LavaAPIError(
                f"{method} {path} -> HTTP {resp.status_code}: {body}",
                status_code=resp.status_code, body=body)
        return resp

    def _paginate(self, path: str,
                  params: dict[str, Any] | None = None,
                  limit: int | None = None) -> Iterator[dict]:
        # Always pass `params`. DRF's `next` URL already carries them, so
        # re-supplying is idempotent (the URL query string wins on merge),
        # and this survives a future pagination-class change on the server.
        url: str | None = self._url(path)
        yielded = 0
        while url:
            try:
                resp = self._session.get(url, params=params,
                                         timeout=self.timeout)
            except requests.RequestException as exc:
                raise LavaAPIError(f"GET {url} failed: {exc}") from exc
            if not resp.ok:
                raise LavaAPIError(
                    f"GET {url} -> HTTP {resp.status_code}: {resp.text[:200]}",
                    status_code=resp.status_code)
            data = resp.json()
            for item in data.get("results", []):
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            url = data.get("next")

    # ---- public API ------------------------------------------------------

    def submit_job(self, definition: str, *, validate: bool = True,
                   strict: bool = False) -> list[int | str]:
        """Validate (optional) then submit a LAVA job YAML.

        Returns a list of job IDs. Multinode submissions return sub-ids like
        "123.0", "123.1"; single-node jobs return integer ids.
        """
        if not isinstance(definition, str):
            raise TypeError("definition must be a YAML string")
        if strict and not validate:
            raise ValueError(
                "strict=True has no effect when validate=False; "
                "pass validate=True or drop strict")

        if validate:
            resp = self._request("POST", "jobs/validate/",
                                 json={"definition": definition,
                                       "strict": strict})
            message = resp.json().get("message", "")
            # The validate endpoint returns HTTP 200 for BOTH valid and
            # invalid jobs — we must inspect the message body.
            # Success message is "Job valid." — invalid is "Job invalid: ...".
            if not message.lower().startswith("job valid"):
                raise LavaAPIError(f"job definition invalid: {message}",
                                   status_code=resp.status_code,
                                   body=resp.json())

        resp = self._request("POST", "jobs/",
                             json={"definition": definition})
        return resp.json().get("job_ids", [])

    def get_job_status(self, job_id: int | str) -> dict:
        """Return the full serialized TestJob as returned by the server.

        Common fields include: id, state, health, submitter, actual_device,
        requested_device_type, submit_time, start_time, end_time,
        description, failure_comment, priority, health_check. Additional
        fields (viewing_groups, tags, is_pipeline, …) are also present and
        may be added by future server versions.
        """
        return self._request("GET", f"jobs/{job_id}/").json()

    def get_job_result(self, job_id: int | str, fmt: str = "json",
                       **params: Any) -> list[dict] | bytes:
        """Fetch results.

        fmt="json"   -> normalized list[dict] from /jobs/<id>/tests/
        fmt="junit"  -> raw junit XML bytes
        fmt="yaml"   -> raw YAML export bytes
        fmt="tap13"  -> raw TAP13 bytes
        fmt="csv"    -> raw CSV bytes
        """
        fmt = fmt.lower()
        if fmt == "json":
            return list(self._paginate(f"jobs/{job_id}/tests/",
                                       params=params or None))
        if fmt in ("junit", "yaml", "tap13", "csv"):
            resp = self._request("GET", f"jobs/{job_id}/{fmt}/",
                                 params=params or None)
            return resp.content
        raise ValueError(
            f"unknown fmt {fmt!r}; expected one of json/junit/yaml/tap13/csv")

    def get_job_logs(self, job_id: int | str,
                     start: int = 0, end: int | None = None) -> str:
        """Fetch raw YAML log lines, optionally windowed.

        `start` and `end` are 0-based line indices on the server. Default
        (start=0, end=None) fetches the full log without any query string.
        """
        params: dict[str, int] = {}
        if start:
            params["start"] = start
        if end is not None:
            params["end"] = end
        resp = self._request("GET", f"jobs/{job_id}/logs/",
                             params=params or None)
        return resp.text

    def get_job_logs_plain(self, job_id: int | str) -> str:
        """Fetch the full log for a job as a single YAML string."""
        return self.get_job_logs(job_id)

    def cancel_job(self, job_id: int | str) -> dict:
        """Send a cancel signal. Returns the server's response dict.

        Uses GET because the LAVA cancel endpoint is registered as
        `@action(detail=True)` with no explicit `methods=`, so DRF's default
        (`['get']`) applies and POST returns 405. Safe against retry-replay
        because the server is idempotent — cancelling an already-finished
        or already-canceling job is a no-op and still returns 200.

        LAVA replies 200 with {"message": "Job cancel signal sent."} even
        when the job has already finished — callers should re-check status
        via get_job_status() if they need certainty.
        """
        resp = self._request("GET", f"jobs/{job_id}/cancel/")
        return resp.json()

    def list_jobs(self, *, state: str | None = None,
                  health: str | None = None,
                  device_type: str | None = None,
                  submitter: str | None = None,
                  limit: int | None = None,
                  extra: dict[str, Any] | None = None) -> list[dict]:
        """List jobs. Filters map to REST query params.

        `state`  — accepts SUBMITTED, SCHEDULING, SCHEDULED, RUNNING,
                   CANCELING, FINISHED (any case). Sent to the server in
                   Title-Case, which is what TestJob.STATE_REVERSE expects.
        `health` — accepts UNKNOWN, COMPLETE, INCOMPLETE, CANCELED (any case).
                   Sent Title-Case for the same reason.
        `device_type` — filters by DeviceType name. Sent on the wire as
                        `requested_device_type__name=<value>` because
                        `requested_device_type` on the server is a
                        RelatedFilter keyed on the DeviceType PK.
        `submitter`   — filters by User.username. Sent on the wire as
                        `submitter__username=<value>` for the same reason
                        (`submitter` is a RelatedFilter keyed on User PK).
        """
        params: dict[str, Any] = {}
        if state is not None:
            params["state"] = self._coerce_state(state)
        if health is not None:
            params["health"] = self._coerce_health(health)
        if device_type is not None:
            params["requested_device_type__name"] = device_type
        if submitter is not None:
            params["submitter__username"] = submitter
        if extra:
            params.update(extra)
        return list(self._paginate("jobs/", params=params, limit=limit))

    def get_device_types(self) -> list[dict]:
        """Return every device-type with a full state x health count matrix.

        Note: `total` includes retired devices, because `devices/?all=true`
        is needed to build a complete state x health matrix. Callers who
        want active capacity should compute `total - by_health["Retired"]`.

        Each entry:
            {
                "name": "rb3g2",
                "architecture": "arm64",
                "processor": ...,
                "total": 5,             # includes retired
                "counts": {
                    "Idle":     {"Good": 3, "Unknown": 0, ...},
                    "Reserved": {...},
                    "Running":  {...},
                },
                "by_state":  {"Idle": 3, "Reserved": 1, "Running": 1},
                "by_health": {"Good": 3, "Unknown": 0, ..., "Retired": 0},
            }
        """
        types = list(self._paginate("devicetypes/"))
        counts_by_type = self._device_counts()

        result: list[dict] = []
        for dt in types:
            name = dt.get("name")
            matrix = counts_by_type.get(name, self._empty_matrix())
            total = sum(matrix[s][h] for s in _DEVICE_STATES
                        for h in _DEVICE_HEALTHS)
            by_state = {s: sum(matrix[s].values()) for s in _DEVICE_STATES}
            by_health = {h: sum(matrix[s][h] for s in _DEVICE_STATES)
                         for h in _DEVICE_HEALTHS}
            result.append({
                **dt,
                "total": total,
                "counts": matrix,
                "by_state": by_state,
                "by_health": by_health,
            })
        return result

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _coerce_state(value: str) -> str:
        up = value.strip().upper()
        if up not in _JOB_STATES:
            raise ValueError(
                f"invalid state {value!r}; expected one of "
                f"{sorted(_JOB_STATES)}")
        return _JOB_STATES[up]

    @staticmethod
    def _coerce_health(value: str) -> str:
        up = value.strip().upper()
        if up not in _JOB_HEALTHS:
            raise ValueError(
                f"invalid health {value!r}; expected one of "
                f"{sorted(_JOB_HEALTHS)}")
        return _JOB_HEALTHS[up]

    @staticmethod
    def _empty_matrix() -> dict[str, dict[str, int]]:
        return {s: {h: 0 for h in _DEVICE_HEALTHS} for s in _DEVICE_STATES}

    def _device_counts(self) -> dict[str, dict[str, dict[str, int]]]:
        counts: dict[str, dict[str, dict[str, int]]] = {}
        # `all=true` includes retired devices so the matrix is complete
        for dev in self._paginate("devices/", params={"all": "true"}):
            dtype = dev.get("device_type")
            state = dev.get("state")
            health = dev.get("health")
            if dtype not in counts:
                counts[dtype] = self._empty_matrix()
            if state in counts[dtype] and health in counts[dtype][state]:
                counts[dtype][state][health] += 1
        return counts


__all__ = ["LavaClient", "LavaError", "LavaAuthError", "LavaAPIError"]
