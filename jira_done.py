#!/usr/bin/env -S uv run
"""Export completed Jira issues to the worklog CSV format."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

CSV_FIELDS = (
    "issue_key",
    "task_name",
    "start_time",
    "done_time",
    "epic_key",
    "epic_name",
    "parent_type",
)


class ExportError(Exception):
    """A user-actionable export failure."""


@dataclass(frozen=True)
class JiraConfig:
    """Credentials and endpoint settings for Jira Cloud."""

    base_url: str
    email: str
    token: str
    uses_legacy_token: bool = False

    @classmethod
    def load(cls, path: Path) -> JiraConfig:
        """Load Jira settings from YAML and the process environment."""
        if not path.is_file():
            raise ExportError(f"config file not found: {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ExportError(f"cannot parse config: {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise ExportError(f"config root must be a mapping: {path}")

        base_url = raw.get("JIRA_BASE_URL") or raw.get("CONFLUENCE_BASE_URL")
        email = raw.get("JIRA_EMAIL") or raw.get("CONFLUENCE_EMAIL")
        environment_token = os.environ.get("JIRA_API_TOKEN", "").strip()
        legacy_token = raw.get("CONFLUENCE_TOKEN")
        token = environment_token or legacy_token

        for setting_name, value in (
            ("Jira base URL", base_url),
            ("Jira email", email),
            ("Jira API token", token),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ExportError(f"missing {setting_name}")

        return cls(
            base_url=base_url.rstrip("/"),
            email=email.strip(),
            token=token.strip(),
            uses_legacy_token=not environment_token,
        )


@dataclass(frozen=True)
class DateRange:
    """Inclusive calendar-date range used by the Jira query."""

    start: date
    end: date

    @classmethod
    def parse(cls, start: str, end: str) -> DateRange:
        """Parse and validate ISO date arguments."""
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if start_date > end_date:
            raise ValueError("start date must be on or before end date")
        return cls(start=start_date, end=end_date)

    def jql(self) -> str:
        """Build JQL whose exclusive upper bound includes the full end date."""
        end_exclusive = self.end + timedelta(days=1)
        return (
            "project = DC AND assignee = currentUser() "
            f'AND resolutiondate >= "{self.start.isoformat()}" '
            f'AND resolutiondate < "{end_exclusive.isoformat()}" '
            "ORDER BY resolutiondate DESC"
        )

    @property
    def default_output(self) -> str:
        """Return the established selected-CSV filename."""
        return f"jira_done_{self.start:%Y%m%d}_{self.end:%Y%m%d}_selected.csv"


def search_issues(config: JiraConfig, jql: str) -> list[dict[str, Any]]:
    """Fetch one page of Jira issues matching the supplied JQL."""
    request_body = json.dumps(
        {
            "jql": jql,
            "fields": ["summary", "created", "resolutiondate", "parent"],
            "maxResults": 100,
        }
    ).encode()
    credentials = base64.b64encode(
        f"{config.email}:{config.token}".encode()
    ).decode()
    request = Request(
        f"{config.base_url}/rest/api/3/search/jql",
        data=request_body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise ExportError(f"Jira request failed: {exc}") from exc

    issues = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues, list):
        raise ExportError("Jira response does not contain an issues list")
    return issues


def issue_to_row(issue: dict[str, Any]) -> dict[str, str]:
    """Convert a Jira issue response object to the selected CSV schema."""
    fields = issue.get("fields") or {}
    parent = fields.get("parent") or {}
    parent_fields = parent.get("fields") or {}
    issue_type = parent_fields.get("issuetype") or {}
    return {
        "issue_key": str(issue.get("key") or ""),
        "task_name": str(fields.get("summary") or ""),
        "start_time": str(fields.get("created") or ""),
        "done_time": str(fields.get("resolutiondate") or ""),
        "epic_key": str(parent.get("key") or ""),
        "epic_name": str(parent_fields.get("summary") or ""),
        "parent_type": str(issue_type.get("name") or ""),
    }


def write_csv(path: Path, issues: list[dict[str, Any]]) -> None:
    """Atomically write issues as a UTF-8 BOM CSV."""
    if not path.parent.exists():
        path.parent.mkdir(parents=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(issue_to_row(issue) for issue in issues)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Export completed DC Jira issues to CSV."
    )
    parser.add_argument(
        "--start", required=True, help="Inclusive start date (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--end", required=True, help="Inclusive end date (YYYY-MM-DD)."
    )
    parser.add_argument("--config", default="config.yaml", help="YAML config path.")
    parser.add_argument("--output", help="CSV output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Jira CSV export command."""
    args = build_parser().parse_args(argv)
    try:
        period = DateRange.parse(args.start, args.end)
        config = JiraConfig.load(Path(args.config))
        if config.uses_legacy_token:
            sys.stderr.write(
                "WARNING: move CONFLUENCE_TOKEN to JIRA_API_TOKEN\n"
            )
        issues = search_issues(config, period.jql())
        output = Path(args.output or period.default_output)
        write_csv(output, issues)
    except (ExportError, OSError, ValueError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    sys.stdout.write(f"Wrote {len(issues)} issues to {output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
