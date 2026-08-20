#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0.3"]
# ///
"""Shared Jira Cloud client: credential loading and authenticated requests."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

ENV_TOKEN_NAME = "JIRA_API_TOKEN"
ERROR_BODY_LIMIT = 500


class JiraError(Exception):
    """A user-actionable Jira failure.

    `status` carries the HTTP status when the failure came from a response, so
    callers can distinguish retryable throttling from a bad request.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class JiraConfig:
    """Credentials and endpoint settings for Jira Cloud."""

    base_url: str
    email: str
    token: str
    uses_legacy_token: bool = False

    @classmethod
    def load(cls, path: Path, *, allow_legacy_token: bool = True) -> JiraConfig:
        """Load Jira settings from YAML and the process environment.

        Write-capable callers pass allow_legacy_token=False so that only an
        explicit JIRA_API_TOKEN can authorize mutations.
        """
        raw = _read_yaml_mapping(path)

        base_url = raw.get("JIRA_BASE_URL") or raw.get("CONFLUENCE_BASE_URL")
        email = raw.get("JIRA_EMAIL") or raw.get("CONFLUENCE_EMAIL")
        environment_token = os.environ.get(ENV_TOKEN_NAME, "").strip()

        if environment_token:
            token: Any = environment_token
        elif allow_legacy_token:
            token = raw.get("CONFLUENCE_TOKEN")
        else:
            raise JiraError(
                f"{ENV_TOKEN_NAME} must be set for write operations "
                "(the config file token is not accepted here)"
            )

        for setting_name, value in (
            ("Jira base URL", base_url),
            ("Jira email", email),
            ("Jira API token", token),
        ):
            if not isinstance(value, str) or not value.strip():
                raise JiraError(f"missing {setting_name}")

        return cls(
            base_url=base_url.rstrip("/"),
            email=email.strip(),
            token=token.strip(),
            uses_legacy_token=not environment_token,
        )

    def authorization(self) -> str:
        """Build the Basic auth header value."""
        encoded = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        return f"Basic {encoded}"


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML file that must contain a top-level mapping."""
    if not path.is_file():
        raise JiraError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise JiraError(f"cannot parse config: {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise JiraError(f"config root must be a mapping: {path}")
    return raw


def request(
    config: JiraConfig,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 30,
) -> Any:
    """Send an authenticated JSON request and return the decoded response.

    Credentials never appear in raised messages; only the status and a short
    body excerpt are surfaced so field-level 400s stay diagnosable.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    http_request = Request(
        f"{config.base_url}{path}",
        data=data,
        headers={
            "Accept": "application/json",
            "Authorization": config.authorization(),
            "Content-Type": "application/json",
        },
        method=method,
    )

    try:
        with urlopen(http_request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        raise JiraError(
            f"Jira request failed: HTTP {exc.code}: {_error_body(exc)}",
            status=exc.code,
        ) from exc
    except URLError as exc:
        raise JiraError(f"Jira request failed: {exc}") from exc

    if not body:
        return {}

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise JiraError(f"Jira request failed: {exc}") from exc

    if not isinstance(decoded, (dict, list)):
        raise JiraError("Jira response is not a JSON object or array")
    return decoded


def _error_body(exc: HTTPError) -> str:
    """Extract a short, safe excerpt from an HTTP error body."""
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 - body is best-effort diagnostic only
        return exc.reason if isinstance(exc.reason, str) else ""

    if not raw:
        return exc.reason if isinstance(exc.reason, str) else ""
    return raw.decode("utf-8", errors="replace")[:ERROR_BODY_LIMIT]
