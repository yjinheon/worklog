from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import issue_plan
import jira_create
import worklog_parser
from epic_classifier import Assignment, Epic
from jira_client import JiraConfig, JiraError
from worklog_parser import WorkItem

BASE_URL = "https://example.atlassian.net"

CONFIG_BODY = """
JIRA_BASE_URL: https://example.atlassian.net
JIRA_EMAIL: worker@example.com
JIRA_PROJECT_KEY: DC

project_groups:
  data-engineering:
    description: pipelines
  worklog-system:
    description: worklog

repos:
  - path: ~/workspace/data-orchestrator
    group: data-engineering
  - path: ~/workspace/worklog
    group: worklog-system

task_tags: [feat, fix, refactor, perf, docs, test, chore, build, ci, style, revert]
skip_tags: [chore, style, ci]

epic_map:
  data-engineering: DC-592
  worklog-system: null
"""

WORKLOG_BODY = """2026-08-12

이슈사항

- 배경

작업완료 사항

- [data-orchestrator] [feat, clickhouse] TAS 캠페인 마트 구축 #project/data-orchestrator #feat #clickhouse

- [worklog] [chore] 워크로그 스크립트 정리 #project/worklog #chore

- [data-orchestrator] [chore] 모니터링 NodePort를 30081로 변경 #project/data-orchestrator #chore #kubernetes

- [data-orchestrator] [fix, chore] 스냅샷 보관 정책 수정 #project/data-orchestrator #fix #chore
"""

# Shapes below mirror live Jira Cloud responses from the DC project.
MANY_BODY = """2026-08-14

작업완료 사항

- [data-orchestrator] [feat] 작업 하나 #project/data-orchestrator #feat

- [data-orchestrator] [feat] 작업 둘 #project/data-orchestrator #feat

- [data-orchestrator] [fix] 작업 셋 #project/data-orchestrator #fix

- [data-orchestrator] [fix] 작업 넷 #project/data-orchestrator #fix

- [data-orchestrator] [refactor] 작업 다섯 #project/data-orchestrator #refactor
"""

ISSUE_TYPES = {
    "startAt": 0,
    "maxResults": 50,
    "total": 5,
    "issueTypes": [
        {"id": "10000", "name": "에픽", "untranslatedName": "Epic", "hierarchyLevel": 1},
        {"id": "10003", "name": "스토리", "untranslatedName": "Story", "hierarchyLevel": 0},
        {"id": "10004", "name": "작업", "untranslatedName": "Task", "hierarchyLevel": 0},
        {"id": "10005", "name": "하위 작업", "untranslatedName": "Subtask", "hierarchyLevel": -1},
        {"id": "10006", "name": "버그", "untranslatedName": "Bug", "hierarchyLevel": 0},
    ],
}

CREATE_FIELDS = {
    "startAt": 0,
    "maxResults": 50,
    "total": 7,
    "fields": [
        {"fieldId": "summary"},
        {"fieldId": "issuetype"},
        {"fieldId": "parent"},
        {"fieldId": "assignee"},
        {"fieldId": "description"},
        {"fieldId": "duedate"},
        {"fieldId": "customfield_10015"},
    ],
}

EPIC_SEARCH = {
    "issues": [
        {"key": "DC-592", "fields": {"summary": "[Data] 내부 데이터 파이프라인 구축작업"}}
    ]
}


def config() -> JiraConfig:
    return JiraConfig(
        base_url="https://example.atlassian.net",
        email="worker@example.com",
        token="secret-token",
    )


def work_item(**overrides: object) -> WorkItem:
    values: dict[str, object] = {
        "summary": "TAS 캠페인 마트 구축",
        "project": "data-orchestrator",
        "source_date": date(2026, 8, 12),
        "tags": ("feat", "clickhouse"),
        "done": True,
        "shas": ("abc1234",),
    }
    values.update(overrides)
    return WorkItem(**values)  # type: ignore[arg-type]


class IssueTypeSelectionTests(unittest.TestCase):
    TYPES = ISSUE_TYPES["issueTypes"]

    def test_picks_task_not_story_among_same_level_types(self) -> None:
        chosen = jira_create.select_issue_type(
            self.TYPES, 0, jira_create.TASK_TYPE_NAMES
        )

        self.assertEqual(chosen["id"], "10004")
        self.assertEqual(chosen["name"], "작업")

    def test_picks_epic_by_name(self) -> None:
        chosen = jira_create.select_issue_type(
            self.TYPES, 1, jira_create.EPIC_TYPE_NAMES
        )

        self.assertEqual(chosen["name"], "에픽")

    def test_falls_back_to_hierarchy_level_when_no_name_matches(self) -> None:
        types = [{"id": "9", "name": "커스텀", "hierarchyLevel": 0}]

        self.assertEqual(
            jira_create.select_issue_type(types, 0, jira_create.TASK_TYPE_NAMES)["id"],
            "9",
        )

    def test_missing_level_raises(self) -> None:
        with self.assertRaises(JiraError):
            jira_create.select_issue_type(
                [{"id": "1", "hierarchyLevel": 0}], 1, jira_create.EPIC_TYPE_NAMES
            )


class EpicQueryTests(unittest.TestCase):
    """JQL matches the untranslated type name only, so query by id instead."""

    def test_queries_epics_by_issue_type_id(self) -> None:
        captured: dict[str, object] = {}

        def fake_request(config, method, path, payload=None, **kwargs):
            captured.update(payload or {})
            return EPIC_SEARCH

        with patch("jira_create.request", side_effect=fake_request):
            epics = jira_create.fetch_epics(config(), "DC", "10000")

        self.assertIn("issuetype = 10000", captured["jql"])
        self.assertNotIn("에픽", captured["jql"])
        self.assertIn("assignee = currentUser()", captured["jql"])
        self.assertEqual(epics[0].key, "DC-592")


class DateFieldSelectionTests(unittest.TestCase):
    def test_prefers_start_date_and_duedate(self) -> None:
        fields = jira_create.select_date_fields(
            {"duedate", "customfield_10015", "customfield_10121", "customfield_10122"}
        )
        self.assertEqual(fields.start, "customfield_10015")
        self.assertEqual(fields.end, "duedate")

    def test_falls_back_to_target_dates(self) -> None:
        fields = jira_create.select_date_fields(
            {"customfield_10121", "customfield_10122"}
        )
        self.assertEqual(fields.start, "customfield_10121")
        self.assertEqual(fields.end, "customfield_10122")

    def test_absent_fields_resolve_to_none(self) -> None:
        fields = jira_create.select_date_fields({"summary"})
        self.assertIsNone(fields.start)
        self.assertIsNone(fields.end)


class PayloadTests(unittest.TestCase):
    def payload(self, item: WorkItem, date_fields: jira_create.DateFields) -> dict:
        return jira_create.build_payload(
            Assignment(item=item, epic_key="DC-592"),
            project_key="DC",
            issue_type_id="10001",
            account_id="712020:abc",
            date_fields=date_fields,
        )

    def test_payload_sets_parent_type_assignee_and_dates(self) -> None:
        fields = self.payload(
            work_item(), jira_create.DateFields("customfield_10015", "duedate")
        )["fields"]

        self.assertEqual(fields["project"], {"key": "DC"})
        self.assertEqual(fields["issuetype"], {"id": "10001"})
        self.assertEqual(fields["parent"], {"key": "DC-592"})
        self.assertEqual(fields["assignee"], {"accountId": "712020:abc"})
        self.assertEqual(fields["summary"], "TAS 캠페인 마트 구축")
        self.assertEqual(fields["customfield_10015"], "2026-08-12")
        self.assertEqual(fields["duedate"], "2026-08-12")

    def test_completion_date_wins_as_end_date(self) -> None:
        item = work_item(completion_date=date(2026, 8, 14))
        fields = self.payload(
            item, jira_create.DateFields("customfield_10015", "duedate")
        )["fields"]

        self.assertEqual(fields["customfield_10015"], "2026-08-12")
        self.assertEqual(fields["duedate"], "2026-08-14")

    def test_unavailable_date_fields_are_omitted(self) -> None:
        fields = self.payload(work_item(), jira_create.DateFields(None, None))["fields"]

        self.assertNotIn("duedate", fields)
        self.assertNotIn("customfield_10015", fields)

    def test_long_summary_is_truncated_to_the_jira_limit(self) -> None:
        long_text = "가" * 400
        fields = self.payload(
            work_item(summary=long_text), jira_create.DateFields(None, None)
        )["fields"]

        self.assertEqual(len(fields["summary"]), jira_create.SUMMARY_LIMIT)
        self.assertTrue(fields["summary"].endswith("…"))

    def test_full_summary_survives_in_the_description(self) -> None:
        long_text = "가" * 400
        fields = self.payload(
            work_item(summary=long_text), jira_create.DateFields(None, None)
        )["fields"]

        self.assertIn(long_text, json.dumps(fields["description"], ensure_ascii=False))

    def test_summary_at_the_limit_is_left_alone(self) -> None:
        exact = "나" * jira_create.SUMMARY_LIMIT
        fields = self.payload(
            work_item(summary=exact), jira_create.DateFields(None, None)
        )["fields"]

        self.assertEqual(fields["summary"], exact)

    def test_description_is_adf_carrying_provenance(self) -> None:
        item = work_item()
        fields = self.payload(item, jira_create.DateFields(None, None))["fields"]
        description = fields["description"]

        self.assertEqual(description["type"], "doc")
        self.assertEqual(description["version"], 1)
        flattened = json.dumps(description, ensure_ascii=False)
        self.assertIn(f"worklog-key: {item.key()}", flattened)
        self.assertIn("abc1234", flattened)
        self.assertIn("2026-08-12", flattened)


class StateStoreTests(unittest.TestCase):
    def test_round_trips_created_keys(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".jira_created.jsonl"
            store = jira_create.StateStore(path)

            self.assertEqual(store.created_keys(), set())
            store.record("abc123", "DC-901", "요약")
            store.record("def456", "DC-902", "요약2")

            self.assertEqual(
                jira_create.StateStore(path).created_keys(), {"abc123", "def456"}
            )

    def test_ignores_malformed_lines(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".jira_created.jsonl"
            path.write_text('not json\n{"key":"ok1","issue_key":"DC-1"}\n', encoding="utf-8")

            self.assertEqual(jira_create.StateStore(path).created_keys(), {"ok1"})

    def test_record_appends_immediately(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".jira_created.jsonl"
            store = jira_create.StateStore(path)
            store.record("abc123", "DC-901", "요약")

            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[0]["key"], "abc123")
            self.assertEqual(entries[0]["issue_key"], "DC-901")


class RetryTests(unittest.TestCase):
    def test_retries_rate_limit_then_succeeds(self) -> None:
        responses = [
            JiraError("rate limited", status=429),
            {"key": "DC-901"},
        ]

        def fake_request(*args: object, **kwargs: object) -> object:
            result = responses.pop(0)
            if isinstance(result, JiraError):
                raise result
            return result

        with (
            patch("jira_create.request", side_effect=fake_request),
            patch("jira_create.time.sleep") as slept,
        ):
            key = jira_create.create_issue(config(), {"fields": {}})

        self.assertEqual(key, "DC-901")
        self.assertEqual(slept.call_count, 1)

    def test_gives_up_after_max_attempts(self) -> None:
        with (
            patch(
                "jira_create.request",
                side_effect=JiraError("server error", status=500),
            ) as called,
            patch("jira_create.time.sleep"),
        ):
            with self.assertRaises(JiraError):
                jira_create.create_issue(config(), {"fields": {}})

        self.assertEqual(called.call_count, jira_create.MAX_ATTEMPTS)

    def test_does_not_retry_client_errors(self) -> None:
        with (
            patch(
                "jira_create.request", side_effect=JiraError("bad field", status=400)
            ) as called,
            patch("jira_create.time.sleep"),
        ):
            with self.assertRaises(JiraError):
                jira_create.create_issue(config(), {"fields": {}})

        self.assertEqual(called.call_count, 1)


class MainFlowFixture(unittest.TestCase):
    """Shared harness: config, worklogs, and a fake Jira."""

    WORKLOGS = {"2026-08-12.md": WORKLOG_BODY}

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config_path = root / "config.yaml"
        self.config_path.write_text(CONFIG_BODY, encoding="utf-8")
        self.worklog_dir = root / "worklogs"
        self.worklog_dir.mkdir()
        for name, body in self.WORKLOGS.items():
            (self.worklog_dir / name).write_text(body, encoding="utf-8")
        self.state_path = root / ".jira_created.jsonl"
        self.plan_path = root / "plan.yaml"
        self.created: list[dict] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake_request(self, config, method, path, payload=None, **kwargs):
        if path.endswith("/myself"):
            return {"accountId": "712020:abc"}
        if path.endswith("/issuetypes"):
            return ISSUE_TYPES
        if "/issuetypes/" in path:
            return CREATE_FIELDS
        if path.endswith("/search/jql"):
            return EPIC_SEARCH
        if path == "/rest/api/3/issue":
            self.created.append(payload)
            return {"key": f"DC-9{len(self.created):02d}"}
        raise AssertionError(f"unexpected call: {method} {path}")

    def run_main(self, *extra: str, triage: bool = False) -> tuple[int, str, str]:
        argv = [
            "--start", "2026-08-01",
            "--end", "2026-08-31",
            "--config", str(self.config_path),
            "--worklog-dir", str(self.worklog_dir),
            "--state", str(self.state_path),
            "--plan-out", str(self.plan_path),
            *([] if triage else ["--no-triage"]),
            *extra,
        ]
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.dict("os.environ", {"JIRA_API_TOKEN": "env-token"}, clear=True),
            patch("jira_create.request", side_effect=self.fake_request),
            patch("jira_create.time.sleep"),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = jira_create.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def created_by_type(self) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for entry in self.created:
            grouped.setdefault(entry["fields"]["issuetype"]["id"], []).append(entry)
        return grouped

    def summaries(self) -> list[str]:
        return [entry["fields"]["summary"] for entry in self.created]


class MainFlowTests(MainFlowFixture):
    def test_dry_run_is_the_default_and_creates_nothing(self) -> None:
        code, out, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(self.created, [])
        self.assertIn("TAS 캠페인 마트 구축", out)
        self.assertIn("--apply", out)

    def test_dry_run_writes_an_editable_plan(self) -> None:
        self.run_main()

        text = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("tasks:", text)
        self.assertIn("epic: DC-592", text)
        self.assertIn("--from-plan", text)

    def test_default_level_is_subtask_under_a_grouping_task(self) -> None:
        self.run_main("--apply", "--yes")

        by_type = self.created_by_type()
        self.assertEqual(len(by_type["10004"]), 1)
        self.assertEqual(len(by_type["10005"]), 2)

    def test_issue_names_carry_the_epic_prefix(self) -> None:
        self.run_main("--apply", "--yes")

        self.assertTrue(all(s.startswith("[Data] ") for s in self.summaries()))

    def test_subtasks_hang_off_the_created_parent(self) -> None:
        self.run_main("--apply", "--yes")

        by_type = self.created_by_type()
        parent = by_type["10004"][0]
        self.assertEqual(parent["fields"]["parent"], {"key": "DC-592"})
        self.assertEqual(
            {entry["fields"]["parent"]["key"] for entry in by_type["10005"]},
            {"DC-901"},
        )

    def test_excluded_group_is_not_created(self) -> None:
        self.run_main("--apply", "--yes")

        self.assertNotIn("워크로그 스크립트 정리", " ".join(self.summaries()))

    def test_second_run_skips_already_created_items(self) -> None:
        self.run_main("--apply", "--yes")
        self.created.clear()

        code, out, _ = self.run_main("--apply", "--yes")

        self.assertEqual(code, 0)
        self.assertEqual(self.created, [])
        self.assertIn("tracked=2", out)

    def test_accepts_the_config_token_when_no_env_token_is_set(self) -> None:
        self.config_path.write_text(
            CONFIG_BODY + "\nCONFLUENCE_TOKEN: config-token\n", encoding="utf-8"
        )
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("jira_create.request", side_effect=self.fake_request),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = jira_create.main(
                [
                    "--start", "2026-08-01", "--end", "2026-08-31",
                    "--config", str(self.config_path),
                    "--worklog-dir", str(self.worklog_dir),
                    "--state", str(self.state_path),
                    "--plan-out", str(self.plan_path),
                    "--no-triage",
                ]
            )

        self.assertEqual(code, 0)
        self.assertNotIn("config-token", stdout.getvalue() + stderr.getvalue())

    def test_missing_token_everywhere_fails(self) -> None:
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("jira_create.request", side_effect=self.fake_request),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = jira_create.main(
                [
                    "--start", "2026-08-01", "--end", "2026-08-31",
                    "--config", str(self.config_path),
                    "--worklog-dir", str(self.worklog_dir),
                    "--state", str(self.state_path),
                    "--plan-out", str(self.plan_path),
                    "--no-triage",
                ]
            )

        self.assertEqual(code, 1)
        self.assertIn("Jira API token", stderr.getvalue())

    def test_invalid_epic_map_aborts_before_creating(self) -> None:
        self.config_path.write_text(
            CONFIG_BODY.replace("data-engineering: DC-592", "data-engineerng: DC-592"),
            encoding="utf-8",
        )
        code, _, err = self.run_main("--apply", "--yes")

        self.assertEqual(code, 1)
        self.assertEqual(self.created, [])
        self.assertIn("data-engineerng", err)

    def test_unresolved_items_are_reported_not_created(self) -> None:
        (self.worklog_dir / "2026-08-10.md").write_text(
            "2026-08-10\n\n작업완료 사항\n\n"
            "- [ghost-repo] [feat] 미등록 프로젝트 작업 #project/ghost-repo #feat\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_main("--apply", "--yes")

        self.assertEqual(code, 0)
        self.assertNotIn("미등록 프로젝트 작업", " ".join(self.summaries()))
        self.assertIn("ghost-repo", out)

    def test_trivial_lines_are_not_created(self) -> None:
        self.run_main("--apply", "--yes")

        joined = " ".join(self.summaries())
        self.assertNotIn("모니터링 NodePort를 30081로 변경", joined)
        self.assertIn("스냅샷 보관 정책 수정", joined)

    def test_trivial_lines_get_no_write_back(self) -> None:
        self.run_main("--apply", "--yes")

        text = (self.worklog_dir / "2026-08-12.md").read_text(encoding="utf-8")
        nodeport = next(line for line in text.splitlines() if "NodePort" in line)
        self.assertNotIn("browse/DC-", nodeport)

    def test_include_trivial_restores_them(self) -> None:
        self.run_main("--apply", "--yes", "--include-trivial")

        self.assertIn("모니터링 NodePort를 30081로 변경", " ".join(self.summaries()))

    def test_created_subtasks_are_written_back(self) -> None:
        self.run_main("--apply", "--yes")

        text = (self.worklog_dir / "2026-08-12.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("browse/DC-"), 2)

    def test_line_with_issue_key_is_neither_created_nor_written_back(self) -> None:
        path = self.worklog_dir / "2026-08-12.md"
        path.write_text(
            WORKLOG_BODY.replace(
                "TAS 캠페인 마트 구축 #project/data-orchestrator #feat #clickhouse",
                "TAS 캠페인 마트 구축 #project/data-orchestrator #feat #clickhouse"
                " [issue:: DC-917]",
            ),
            encoding="utf-8",
        )

        code, out, _ = self.run_main("--apply", "--yes")

        self.assertEqual(code, 0)
        self.assertIn("tracked=1", out)
        self.assertIn("DC-917", path.read_text(encoding="utf-8"))
        self.assertNotIn("TAS 캠페인 마트 구축", " ".join(self.summaries()))


class TriageFlowTests(MainFlowFixture):
    """The LLM pass sets levels and drops work too minor to track."""

    def run_with_levels(self, levels, *extra: str):
        seen: list[list[str]] = []

        def llm(items):
            seen.append([i.summary for i in items])
            return {i.key(): levels.get(i.summary, "subtask") for i in items}

        with patch("jira_create.build_triage_llm", return_value=llm):
            result = self.run_main(*extra, triage=True)
        return result, seen

    def test_task_level_items_are_created_flat(self) -> None:
        (code, _, _), _ = self.run_with_levels(
            {"TAS 캠페인 마트 구축": "task", "스냅샷 보관 정책 수정": "task"},
            "--apply", "--yes",
        )

        by_type = self.created_by_type()
        self.assertEqual(code, 0)
        self.assertEqual(len(by_type["10004"]), 2)
        self.assertNotIn("10005", by_type)

    def test_minor_items_are_not_created(self) -> None:
        (code, out, _), _ = self.run_with_levels(
            {"스냅샷 보관 정책 수정": "skip"}, "--apply", "--yes"
        )

        self.assertEqual(code, 0)
        self.assertNotIn("스냅샷 보관 정책 수정", " ".join(self.summaries()))
        self.assertIn("minor=1", out)

    def test_triage_never_sees_tag_filtered_lines(self) -> None:
        _, seen = self.run_with_levels({})

        self.assertEqual(len(seen), 1)
        self.assertNotIn("모니터링 NodePort를 30081로 변경", seen[0])
        self.assertNotIn("워크로그 스크립트 정리", seen[0])

    def test_no_triage_flag_skips_the_llm_entirely(self) -> None:
        calls: list[object] = []

        def llm(items):
            calls.append(items)
            return {}

        with patch("jira_create.build_triage_llm", return_value=llm):
            code, out, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("minor=0", out)


class PlanRoundTripTests(MainFlowFixture):
    WORKLOGS = {"2026-08-12.md": WORKLOG_BODY, "2026-08-14.md": MANY_BODY}

    def test_plan_lists_every_pending_item(self) -> None:
        self.run_main()

        plan = issue_plan.load(self.plan_path)
        self.assertEqual(len(plan.item_keys()), 7)

    def test_edited_plan_is_applied_verbatim(self) -> None:
        self.run_main()
        plan = issue_plan.load(self.plan_path)

        # Move every sub-task under a single renamed parent.
        moved = [s for task in plan.tasks for s in task.subtasks]
        edited = issue_plan.IssuePlan(
            project_key=plan.project_key,
            start=plan.start,
            end=plan.end,
            tasks=(
                issue_plan.PlannedTask(
                    summary="[Data] 손으로 고친 묶음",
                    epic_key="DC-592",
                    subtasks=tuple(moved),
                ),
            ),
        )
        issue_plan.save(edited, self.plan_path)

        code, _, _ = self.run_main("--apply", "--yes", "--from-plan", str(self.plan_path))

        by_type = self.created_by_type()
        self.assertEqual(code, 0)
        self.assertEqual(len(by_type["10004"]), 1)
        self.assertEqual(by_type["10004"][0]["fields"]["summary"], "[Data] 손으로 고친 묶음")
        self.assertEqual(len(by_type["10005"]), len(moved))

    def test_renaming_in_the_plan_renames_the_issue(self) -> None:
        self.run_main()
        plan = issue_plan.load(self.plan_path)
        first = plan.tasks[0]
        renamed = replace(first, summary="[Data] 새 이름")
        issue_plan.save(
            issue_plan.IssuePlan(
                project_key=plan.project_key,
                start=plan.start,
                end=plan.end,
                tasks=(renamed,),
            ),
            self.plan_path,
        )

        self.run_main("--apply", "--yes", "--from-plan", str(self.plan_path))

        self.assertIn("[Data] 새 이름", self.summaries())

    def test_deleting_an_entry_from_the_plan_skips_it(self) -> None:
        self.run_main()
        plan = issue_plan.load(self.plan_path)
        task = plan.tasks[0]
        dropped = task.subtasks[0]
        issue_plan.save(
            replace(plan, tasks=(replace(task, subtasks=task.subtasks[1:]),)),
            self.plan_path,
        )

        self.run_main("--apply", "--yes", "--from-plan", str(self.plan_path))

        created = jira_create.StateStore(self.state_path).created_keys()
        items_created = {k for k in created if not k.startswith("group|")}
        self.assertNotIn(dropped.key, items_created)
        self.assertEqual(len(items_created), len(plan.item_keys()) - 1)

    def test_plan_referring_to_an_unknown_key_is_rejected(self) -> None:
        self.run_main()
        text = self.plan_path.read_text(encoding="utf-8")
        plan = issue_plan.load(self.plan_path)
        stale = plan.item_keys()[0]
        self.plan_path.write_text(
            text.replace(stale, "abcdef0123456789"), encoding="utf-8"
        )

        code, _, err = self.run_main(
            "--apply", "--yes", "--from-plan", str(self.plan_path)
        )

        self.assertEqual(code, 1)
        self.assertEqual(self.created, [])
        self.assertIn("not found", err)

    def test_resume_reuses_the_recorded_parent(self) -> None:
        self.run_main("--apply", "--yes")
        parents = self.created_by_type()["10004"]
        self.created.clear()

        # Forget one sub-task so a single child remains outstanding.
        lines = [
            line
            for line in self.state_path.read_text(encoding="utf-8").splitlines()
            if "작업 다섯" not in line
        ]
        self.state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path = self.worklog_dir / "2026-08-14.md"
        path.write_text(
            "\n".join(
                line.split(" [DC-")[0] if "작업 다섯" in line else line
                for line in path.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )

        self.run_main("--apply", "--yes")

        by_type = self.created_by_type()
        self.assertNotIn("10004", by_type)
        self.assertEqual(len(by_type["10005"]), 1)
        self.assertTrue(parents)


class SummaryPrefixTests(unittest.TestCase):
    """Issue names carry the epic's own [Data]/[AI] prefix."""

    def test_reads_prefix_from_the_epic_summary(self) -> None:
        self.assertEqual(
            jira_create.epic_prefix("[Data] 데이터 파이프라인 구축작업"), "Data"
        )
        self.assertEqual(jira_create.epic_prefix("[AI]AI engineering 작업"), "AI")

    def test_epic_without_a_bracket_prefix_yields_none(self) -> None:
        self.assertIsNone(jira_create.epic_prefix("데이터 오케스트레이터 고도화"))

    def test_applies_the_prefix_to_a_summary(self) -> None:
        self.assertEqual(
            jira_create.prefixed_summary("campaign_active 플래그 추가", "Data"),
            "[Data] campaign_active 플래그 추가",
        )

    def test_does_not_double_prefix(self) -> None:
        self.assertEqual(
            jira_create.prefixed_summary("[Data] 이미 붙음", "Data"),
            "[Data] 이미 붙음",
        )

    def test_replaces_a_different_existing_prefix(self) -> None:
        self.assertEqual(
            jira_create.prefixed_summary("[AI] 잘못 붙음", "Data"),
            "[Data] 잘못 붙음",
        )

    def test_missing_prefix_leaves_the_summary_alone(self) -> None:
        self.assertEqual(jira_create.prefixed_summary("그대로", None), "그대로")

    def test_prefix_counts_toward_the_length_limit(self) -> None:
        long_text = "가" * jira_create.SUMMARY_LIMIT
        result = jira_create.truncate_summary(
            jira_create.prefixed_summary(long_text, "Data")
        )

        self.assertEqual(len(result), jira_create.SUMMARY_LIMIT)
        self.assertTrue(result.startswith("[Data] "))


if __name__ == "__main__":
    unittest.main()
