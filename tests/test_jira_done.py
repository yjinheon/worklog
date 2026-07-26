from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import jira_done


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def sample_issue() -> dict[str, object]:
    return {
        "key": "DC-847",
        "fields": {
            "summary": "[Data] TASON 구글 트렌드 API 구축",
            "created": "2026-06-29T17:16:07.528+0900",
            "resolutiondate": "2026-06-30T15:47:04.271+0900",
            "parent": {
                "key": "DC-592",
                "fields": {
                    "summary": "[Data] 내부 데이터 파이프라인 구축작업",
                    "issuetype": {"name": "에픽"},
                },
            },
        },
    }


class JiraDoneTests(unittest.TestCase):
    def test_date_range_builds_inclusive_jql_and_default_output(self) -> None:
        period = jira_done.DateRange.parse("2026-06-01", "2026-06-30")

        self.assertEqual(
            period.jql(),
            'project = DC AND assignee = currentUser() '
            'AND resolutiondate >= "2026-06-01" '
            'AND resolutiondate < "2026-07-01" '
            'ORDER BY resolutiondate DESC',
        )
        self.assertEqual(
            period.default_output,
            "jira_done_20260601_20260630_selected.csv",
        )

    def test_main_exports_one_page_in_selected_csv_format(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "config.yaml"
            output = root / "result.csv"
            config.write_text(
                "JIRA_BASE_URL: https://example.atlassian.net\n"
                "JIRA_EMAIL: worker@example.com\n",
                encoding="utf-8",
            )
            response = FakeResponse({"issues": [sample_issue()], "isLast": True})

            with (
                patch.dict(
                    "os.environ", {"JIRA_API_TOKEN": "test-token"}, clear=True
                ),
                patch("jira_done.urlopen", return_value=response) as request,
            ):
                result = jira_done.main(
                    [
                        "--start",
                        "2026-06-01",
                        "--end",
                        "2026-06-30",
                        "--config",
                        str(config),
                        "--output",
                        str(output),
                    ]
                )

            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        self.assertEqual(result, 0)
        self.assertEqual(
            rows,
            [
                {
                    "issue_key": "DC-847",
                    "task_name": "[Data] TASON 구글 트렌드 API 구축",
                    "start_time": "2026-06-29T17:16:07.528+0900",
                    "done_time": "2026-06-30T15:47:04.271+0900",
                    "epic_key": "DC-592",
                    "epic_name": "[Data] 내부 데이터 파이프라인 구축작업",
                    "parent_type": "에픽",
                }
            ],
        )
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(
            sent["fields"], ["summary", "created", "resolutiondate", "parent"]
        )
        self.assertIn('resolutiondate < "2026-07-01"', sent["jql"])

    def test_main_follows_next_page_token_and_allows_missing_parent(self) -> None:
        parentless = sample_issue()
        parentless["key"] = "DC-843"
        parentless_fields = parentless["fields"]
        self.assertIsInstance(parentless_fields, dict)
        parentless_fields["parent"] = None
        responses = [
            FakeResponse(
                {"issues": [sample_issue()], "nextPageToken": "page-2"}
            ),
            FakeResponse({"issues": [parentless], "isLast": True}),
        ]

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = root / "config.yaml"
            output = root / "result.csv"
            config.write_text(
                "JIRA_BASE_URL: https://example.atlassian.net\n"
                "JIRA_EMAIL: worker@example.com\n",
                encoding="utf-8",
            )
            with (
                patch.dict(
                    "os.environ", {"JIRA_API_TOKEN": "test-token"}, clear=True
                ),
                patch("jira_done.urlopen", side_effect=responses) as request,
            ):
                result = jira_done.main(
                    [
                        "--start",
                        "2026-06-01",
                        "--end",
                        "2026-06-30",
                        "--config",
                        str(config),
                        "--output",
                        str(output),
                    ]
                )

            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        self.assertEqual(result, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["epic_key"], "")
        self.assertEqual(rows[1]["epic_name"], "")
        self.assertEqual(rows[1]["parent_type"], "")
        second_body = json.loads(request.call_args_list[1].args[0].data)
        self.assertEqual(second_body["nextPageToken"], "page-2")


if __name__ == "__main__":
    unittest.main()
