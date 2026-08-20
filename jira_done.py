#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml>=6.0.3"]
# ///
"""Export completed Jira issues to the worklog CSV format."""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jira_client import JiraConfig, JiraError, request

CSV_FIELDS = (
    "issue_key",
    "task_name",
    "start_time",
    "done_time",
    "epic_key",
    "epic_name",
    "parent_type",
)


ExportError = JiraError


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
    """Fetch every Jira issue page matching the supplied JQL."""
    issues: list[dict[str, Any]] = []
    next_page_token: str | None = None
    seen_tokens: set[str] = set()

    while True:
        payload: dict[str, Any] = {
            "jql": jql,
            "fields": ["summary", "created", "resolutiondate", "parent"],
            "maxResults": 100,
        }
        if next_page_token is not None:
            payload["nextPageToken"] = next_page_token

        response = request(config, "POST", "/rest/api/3/search/jql", payload)

        page_issues = response.get("issues") if isinstance(response, dict) else None
        if not isinstance(page_issues, list):
            raise ExportError("Jira response does not contain an issues list")
        issues.extend(page_issues)

        token = response.get("nextPageToken")
        if token is None:
            return issues
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise ExportError("Jira returned an invalid next page token")
        seen_tokens.add(token)
        next_page_token = token


def issue_to_row(issue: dict[str, Any]) -> dict[str, str]:
    """Convert a Jira issue response object to the selected CSV schema."""
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise ExportError("Jira issue fields must be an object")

    parent_value = fields.get("parent")
    if parent_value is None:
        parent: dict[str, Any] = {}
    elif isinstance(parent_value, dict):
        parent = parent_value
    else:
        raise ExportError("Jira issue parent must be an object or null")

    parent_fields_value = parent.get("fields")
    if parent_fields_value is None:
        parent_fields: dict[str, Any] = {}
    elif isinstance(parent_fields_value, dict):
        parent_fields = parent_fields_value
    else:
        raise ExportError("Jira parent fields must be an object")

    issue_type_value = parent_fields.get("issuetype")
    if issue_type_value is None:
        issue_type: dict[str, Any] = {}
    elif isinstance(issue_type_value, dict):
        issue_type = issue_type_value
    else:
        raise ExportError("Jira parent issue type must be an object")

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
