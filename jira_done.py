#!/usr/bin/env -S uv run
"""Export completed Jira issues to the worklog CSV format."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


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
