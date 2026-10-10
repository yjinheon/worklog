from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import jira_migrate

FAKE_CONFIG = jira_migrate.JiraConfig(
    base_url="https://example.atlassian.net", email="me@example.com", token="t"
)


def make_raw(
    key: str,
    *,
    issue_id: str = "1",
    summary: str = "요약",
    type_name: str = "작업",
    subtask: bool = False,
    status: str = "완료",
    category: str = "done",
    parent: tuple[str, str] | None = None,
    subtasks: tuple[str, ...] = (),
) -> dict[str, object]:
    fields: dict[str, object] = {
        "summary": summary,
        "issuetype": {"name": type_name, "subtask": subtask},
        "status": {"name": status, "statusCategory": {"key": category}},
        "subtasks": [
            {"key": k, "id": f"9{n}", "fields": {"status": {"id": "10298"}}}
            for n, k in enumerate(subtasks)
        ],
    }
    if parent:
        parent_key, parent_type = parent
        fields["parent"] = {
            "key": parent_key,
            "fields": {"issuetype": {"name": parent_type}},
        }
    return {"key": key, "id": issue_id, "fields": fields}


class IssueTest(unittest.TestCase):
    def test_reads_parent_and_subtasks(self) -> None:
        issue = jira_migrate.Issue.from_api(
            make_raw("DC-1", parent=("DC-9", "에픽"), subtasks=("DC-2",))
        )
        self.assertEqual(issue.parent_key, "DC-9")
        self.assertEqual(issue.parent_type, "에픽")
        self.assertEqual([s["key"] for s in issue.subtasks], ["DC-2"])
        self.assertTrue(issue.done)

    def test_open_issue_is_not_done(self) -> None:
        issue = jira_migrate.Issue.from_api(
            make_raw("DC-1", status="진행 중", category="indeterminate")
        )
        self.assertFalse(issue.done)

    def test_epic_detection(self) -> None:
        self.assertTrue(
            jira_migrate.Issue.from_api(make_raw("DC-1", type_name="에픽")).is_epic
        )


class SelectScopeTest(unittest.TestCase):
    def test_keeps_open_issues_and_subtask_parents(self) -> None:
        issues = [
            jira_migrate.Issue.from_api(
                make_raw("DC-1", status="Backlog", category="new")
            ),
            jira_migrate.Issue.from_api(make_raw("DC-2", subtasks=("DC-3",))),
            jira_migrate.Issue.from_api(make_raw("DC-9")),
        ]
        keys = [issue.key for issue in jira_migrate.select_scope(issues)]
        self.assertEqual(keys, ["DC-1", "DC-2"])

    def test_drops_subtasks_that_ride_with_their_parent(self) -> None:
        issues = [
            jira_migrate.Issue.from_api(make_raw("DC-2", subtasks=("DC-3",))),
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-3",
                    subtask=True,
                    type_name="하위 작업",
                    status="Backlog",
                    category="new",
                    parent=("DC-2", "작업"),
                )
            ),
        ]
        keys = [issue.key for issue in jira_migrate.select_scope(issues)]
        self.assertEqual(keys, ["DC-2"])

    def test_pulls_in_children_of_an_in_scope_epic(self) -> None:
        issues = [
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-9",
                    type_name="에픽",
                    status="진행 중",
                    category="indeterminate",
                )
            ),
            jira_migrate.Issue.from_api(make_raw("DC-10", parent=("DC-9", "에픽"))),
        ]
        keys = [issue.key for issue in jira_migrate.select_scope(issues)]
        self.assertEqual(keys, ["DC-9", "DC-10"])

    def test_include_done_sweeps_completed_standalone_issues(self) -> None:
        issues = [
            jira_migrate.Issue.from_api(make_raw("DC-648")),
            jira_migrate.Issue.from_api(make_raw("DC-671")),
        ]
        self.assertEqual(jira_migrate.select_scope(issues), [])
        self.assertEqual(
            [i.key for i in jira_migrate.select_scope(issues, include_done=True)],
            ["DC-648", "DC-671"],
        )

    def test_include_done_still_honours_exclusions(self) -> None:
        issues = [jira_migrate.Issue.from_api(make_raw("DC-648"))]
        self.assertEqual(
            jira_migrate.select_scope(
                issues, frozenset({"DC-648"}), include_done=True
            ),
            [],
        )

    def test_excluded_parent_and_its_subtasks_are_held_back(self) -> None:
        issues = [
            jira_migrate.Issue.from_api(make_raw("DC-722", subtasks=("DC-752",))),
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-752",
                    subtask=True,
                    type_name="하위 작업",
                    status="Backlog",
                    category="new",
                    parent=("DC-722", "작업"),
                )
            ),
            jira_migrate.Issue.from_api(
                make_raw("DC-1", status="Backlog", category="new")
            ),
        ]
        scope = jira_migrate.select_scope(issues, frozenset({"DC-722"}))
        self.assertEqual([issue.key for issue in scope], ["DC-1"])

    def test_exclusion_is_recorded_in_the_plan(self) -> None:
        plan = jira_migrate.build_plan([], [], ("DC-722",))
        self.assertEqual(plan["excluded"], ["DC-722"])
        self.assertIn("이번 단계에서 제외: DC-722", jira_migrate.render_plan(plan))

    def test_orphan_subtask_is_reported(self) -> None:
        scope = [
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-788",
                    subtask=True,
                    type_name="하위 작업",
                    status="Backlog",
                    category="new",
                )
            )
        ]
        self.assertEqual(
            [issue.key for issue in jira_migrate.find_orphan_subtasks(scope)],
            ["DC-788"],
        )


class ClassifyEpicTest(unittest.TestCase):
    def test_routes_by_first_matching_rule(self) -> None:
        cases = {
            "[Data] humuson_id (전사통합id) 구축 1차 작업": "DL-49",
            "[Data] 데이터 파이프라인 모니터링구축": "DL-33",
            "[AI] LLM 서빙용 LiteLLM Gateway 환경 구축": "DL-51",
            "[Data][Catalog]데이터랩 ": None,
            "[Data] iceberg REST 카탈로그 서버 구축작업": "DL-31",
        }
        for summary, expected in cases.items():
            with self.subTest(summary=summary):
                self.assertEqual(
                    jira_migrate.classify_epic(summary, jira_migrate.EPIC_RULES),
                    expected,
                )

    def test_raises_when_no_rule_matches(self) -> None:
        with self.assertRaises(jira_migrate.MigrationError):
            jira_migrate.classify_epic("무엇이든", (("절대없음", "DL-1"),))


class BuildPlanTest(unittest.TestCase):
    def test_groups_by_target_epic_with_epics_first(self) -> None:
        scope = [
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-974",
                    issue_id="10",
                    summary="[AI]AI engineering 작업",
                    type_name="에픽",
                )
            ),
            jira_migrate.Issue.from_api(
                make_raw("DC-984", issue_id="11", summary="사내 LLM endpoint API key")
            ),
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-961",
                    issue_id="12",
                    summary="[Data] humuson_id 구축",
                    subtasks=("DC-1", "DC-2"),
                )
            ),
        ]
        plan = jira_migrate.build_plan(scope, [])
        targets = [group["target_epic"] for group in plan["groups"]]
        self.assertEqual(targets[0], None)
        self.assertEqual(sorted(t for t in targets if t), ["DL-49", "DL-51"])
        self.assertEqual(
            plan["groups"][0]["target_type_id"], jira_migrate.TARGET_TYPE_EPIC
        )
        self.assertEqual(jira_migrate.plan_totals(plan), (3, 2))

    def test_orphan_subtask_gets_an_isolated_group(self) -> None:
        scope = [
            jira_migrate.Issue.from_api(
                make_raw("DC-778", issue_id="1", summary="[Data] iceberg 전환")
            ),
            jira_migrate.Issue.from_api(
                make_raw(
                    "DC-788",
                    issue_id="2",
                    summary="[Data] caf24 컬럼 레벨 이관",
                    subtask=True,
                    type_name="하위 작업",
                )
            ),
        ]
        groups = jira_migrate.build_plan(scope, [])["groups"]
        self.assertEqual(len(groups), 2)
        self.assertFalse(groups[0]["isolated"])
        self.assertEqual([i["key"] for i in groups[0]["issues"]], ["DC-778"])
        self.assertTrue(groups[1]["isolated"])
        self.assertEqual([i["key"] for i in groups[1]["issues"]], ["DC-788"])

    def test_epicless_task_group_is_labelled_separately(self) -> None:
        scope = [
            jira_migrate.Issue.from_api(
                make_raw("DC-849", summary="[Data][Catalog]데이터랩")
            )
        ]
        plan = jira_migrate.build_plan(scope, [])
        self.assertIsNone(plan["groups"][0]["target_epic"])
        self.assertEqual(
            plan["groups"][0]["target_type_id"], jira_migrate.TARGET_TYPE_TASK
        )
        self.assertIn("(에픽 미지정)", jira_migrate.render_plan(plan))

    def test_records_conversions(self) -> None:
        orphan = jira_migrate.Issue.from_api(
            make_raw("DC-788", subtask=True, type_name="하위 작업")
        )
        plan = jira_migrate.build_plan([orphan], [orphan])
        self.assertEqual(plan["convert_to_task"][0]["key"], "DC-788")

    def test_render_includes_totals_and_conversions(self) -> None:
        orphan = jira_migrate.Issue.from_api(
            make_raw("DC-788", subtask=True, type_name="하위 작업")
        )
        rendered = jira_migrate.render_plan(jira_migrate.build_plan([orphan], [orphan]))
        self.assertIn("DC-788", rendered)
        self.assertIn("합계: 최상위 1건", rendered)


class PayloadTest(unittest.TestCase):
    def test_mapping_key_omits_parent_when_absent(self) -> None:
        payload = jira_migrate.build_move_payload(
            [{"key": "DC-974", "id": "10"}],
            jira_migrate.TARGET_TYPE_EPIC,
            jira_migrate.STATUS_MAP,
            None,
        )
        mapping = payload["targetToSourcesMapping"]
        self.assertEqual(list(mapping), ["DL,12572"])
        self.assertTrue(payload["sendBulkNotification"])
        self.assertFalse(mapping["DL,12572"]["inferStatusDefaults"])
        self.assertTrue(mapping["DL,12572"]["inferSubtaskTypeDefault"])

    def test_mapping_key_includes_resolved_parent(self) -> None:
        payload = jira_migrate.build_move_payload(
            [{"key": "DC-961", "id": "12"}],
            jira_migrate.TARGET_TYPE_TASK,
            jira_migrate.STATUS_MAP,
            "DL-49",
        )
        self.assertEqual(
            list(payload["targetToSourcesMapping"]), ["DL,12571,DL-49"]
        )

    def test_notification_can_be_suppressed_when_permitted(self) -> None:
        payload = jira_migrate.build_move_payload(
            [{"key": "DC-1", "id": "1"}],
            jira_migrate.TARGET_TYPE_TASK,
            jira_migrate.STATUS_MAP,
            None,
            notify=False,
        )
        self.assertFalse(payload["sendBulkNotification"])

    def test_only_one_status_mapping_is_sent(self) -> None:
        payload = jira_migrate.build_move_payload(
            [{"key": "DC-993", "id": "1", "subtasks": [{"key": "DC-9"}]}],
            jira_migrate.TARGET_TYPE_TASK,
            jira_migrate.STATUS_MAP,
            "DL-31",
        )
        mapping = payload["targetToSourcesMapping"]["DL,12571,DL-31"]
        self.assertEqual(len(mapping["targetStatus"]), 1)
        self.assertTrue(mapping["inferSubtaskTypeDefault"])

    def test_status_map_is_target_keyed(self) -> None:
        payload = jira_migrate.build_move_payload(
            [{"key": "DC-1", "id": "1"}],
            jira_migrate.TARGET_TYPE_TASK,
            {"13471": ["10298"]},
            None,
        )
        statuses = payload["targetToSourcesMapping"]["DL,12571"]["targetStatus"]
        self.assertEqual(statuses, [{"statuses": {"13471": ["10298"]}}])


class RestoreSubtaskStatusTest(unittest.TestCase):
    def test_maps_a_source_status_onto_its_target(self) -> None:
        self.assertEqual(
            jira_migrate.target_status_for("10298", jira_migrate.STATUS_MAP), "13471"
        )
        self.assertIsNone(
            jira_migrate.target_status_for("99999", jira_migrate.STATUS_MAP)
        )

    def test_transitions_a_reset_subtask_back(self) -> None:
        posted: list[tuple[str, object]] = []

        def fake_request(_config, method, path, payload=None, **_kwargs):  # noqa: ANN001
            if method == "POST":
                posted.append((path, payload))
                return {}
            if path.endswith("/transitions"):
                return {"transitions": [{"id": "31", "to": {"id": "13471"}}]}
            if "fields=status" in path:
                return {"fields": {"status": {"id": "13469"}}}
            return {"key": "DL-53"}

        with patch("jira_migrate.request", side_effect=fake_request):
            restored = jira_migrate.restore_subtask_statuses(
                FAKE_CONFIG,
                [{"key": "DC-860", "id": "900", "status_id": "10298"}],
                jira_migrate.STATUS_MAP,
            )

        self.assertEqual(restored, {"DL-53": "13471"})
        self.assertEqual(posted[0][1], {"transition": {"id": "31"}})

    def test_leaves_a_correct_status_alone(self) -> None:
        def fake_request(_config, method, path, payload=None, **_kwargs):  # noqa: ANN001
            if "fields=status" in path:
                return {"fields": {"status": {"id": "13471"}}}
            if method == "POST":
                raise AssertionError("should not transition")
            return {"key": "DL-53"}

        with patch("jira_migrate.request", side_effect=fake_request):
            self.assertEqual(
                jira_migrate.restore_subtask_statuses(
                    FAKE_CONFIG,
                    [{"key": "DC-860", "id": "900", "status_id": "10298"}],
                    jira_migrate.STATUS_MAP,
                ),
                {},
            )


class ResolveParentTest(unittest.TestCase):
    def test_passes_through_a_literal_key(self) -> None:
        self.assertEqual(jira_migrate.resolve_parent("DL-31", {}), "DL-31")

    def test_resolves_a_moved_epic(self) -> None:
        self.assertEqual(
            jira_migrate.resolve_parent("@DC-974", {"DC-974": "DL-52"}), "DL-52"
        )

    def test_rejects_an_unmoved_epic(self) -> None:
        with self.assertRaises(jira_migrate.MigrationError):
            jira_migrate.resolve_parent("@DC-974", {})

    def test_none_target_has_no_parent(self) -> None:
        self.assertIsNone(jira_migrate.resolve_parent(None, {}))


class ApplyPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "status_map": {"13471": ["10298"]},
            "convert_to_task": [{"key": "DC-788", "reason": "orphan"}],
            "groups": [
                {
                    "target_epic": None,
                    "target_type_id": jira_migrate.TARGET_TYPE_EPIC,
                    "issues": [{"key": "DC-974", "id": "10", "subtasks": 0}],
                },
                {
                    "target_epic": "@DC-974",
                    "target_type_id": jira_migrate.TARGET_TYPE_TASK,
                    "issues": [{"key": "DC-984", "id": "11", "subtasks": 0}],
                },
            ],
        }

    def test_dry_run_sends_nothing(self) -> None:
        with patch("jira_migrate.request") as fake:
            with TemporaryDirectory() as tmp:
                jira_migrate.apply_plan(
                    FAKE_CONFIG, self.plan, Path(tmp) / "ledger.jsonl", dry_run=True
                )
        fake.assert_not_called()

    def test_apply_converts_moves_and_records_new_keys(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_request(_config, method, path, _payload=None, **_kwargs):  # noqa: ANN001
            calls.append((method, path))
            if path.startswith("/rest/api/3/bulk/issues/move"):
                return {"taskId": "t1"}
            if path.startswith("/rest/api/3/bulk/queue/"):
                return {"status": "COMPLETE", "totalIssueCount": 1}
            if path.startswith("/rest/api/3/issue/10"):
                return {"key": "DL-52"}
            if path.startswith("/rest/api/3/issue/11"):
                return {"key": "DL-53"}
            return {}

        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            with patch("jira_migrate.request", side_effect=fake_request):
                moved = jira_migrate.apply_plan(FAKE_CONFIG, self.plan, ledger, dry_run=False)
            records = [json.loads(line) for line in ledger.read_text().splitlines()]

        self.assertEqual(moved, {"DC-974": "DL-52", "DC-984": "DL-53"})
        self.assertEqual(records[1]["resolved_parent"], "DL-52")
        self.assertEqual(records[0]["source"], "DC-974")
        self.assertEqual(records[0]["target"], "DL-52")
        self.assertIn(("PUT", "/rest/api/3/issue/DC-788"), calls)

    def test_failed_bulk_task_raises(self) -> None:
        def fake_request(_config, method, path, _payload=None, **_kwargs):  # noqa: ANN001
            if path.startswith("/rest/api/3/bulk/issues/move"):
                return {"taskId": "t1"}
            if path.startswith("/rest/api/3/bulk/queue/"):
                return {"status": "FAILED", "failedAccessibleIssues": {"10": ["nope"]}}
            return {}

        plan = {"convert_to_task": [], "groups": self.plan["groups"][:1]}
        with TemporaryDirectory() as tmp:
            with patch("jira_migrate.request", side_effect=fake_request):
                with self.assertRaises(jira_migrate.MigrationError):
                    jira_migrate.apply_plan(
                        FAKE_CONFIG, plan, Path(tmp) / "ledger.jsonl", dry_run=False
                    )

    def test_missing_task_id_raises(self) -> None:
        plan = {"convert_to_task": [], "groups": self.plan["groups"][:1]}
        with TemporaryDirectory() as tmp:
            with patch("jira_migrate.request", return_value={}):
                with self.assertRaises(jira_migrate.MigrationError):
                    jira_migrate.apply_plan(
                        FAKE_CONFIG, plan, Path(tmp) / "ledger.jsonl", dry_run=False
                    )


class AwaitTaskTest(unittest.TestCase):
    def test_polls_until_the_task_settles(self) -> None:
        responses = [
            {"status": "ENQUEUED"},
            {"status": "RUNNING"},
            {"status": "COMPLETE"},
        ]
        with patch("jira_migrate.request", side_effect=responses):
            with patch("jira_migrate.time.sleep"):
                self.assertEqual(
                    jira_migrate.await_task(FAKE_CONFIG, "t1"), {"status": "COMPLETE"}
                )

    def test_gives_up_after_the_poll_limit(self) -> None:
        with patch("jira_migrate.request", return_value={"status": "RUNNING"}):
            with patch("jira_migrate.time.sleep"):
                with self.assertRaises(jira_migrate.MigrationError):
                    jira_migrate.await_task(FAKE_CONFIG, "t1")


class SearchIssuesTest(unittest.TestCase):
    def test_follows_the_page_token(self) -> None:
        pages = [
            {"issues": [make_raw("DC-1")], "nextPageToken": "n"},
            {"issues": [make_raw("DC-2")]},
        ]
        with patch("jira_migrate.request", side_effect=pages):
            issues = jira_migrate.search_issues(FAKE_CONFIG, "project = DC")
        self.assertEqual([issue.key for issue in issues], ["DC-1", "DC-2"])


class CliTest(unittest.TestCase):
    def test_apply_dry_run_reads_the_plan_file(self) -> None:
        with TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.yaml"
            plan_path.write_text(
                "groups: []\nconvert_to_task: []\n", encoding="utf-8"
            )
            with patch("jira_migrate.JiraConfig.load", return_value=FAKE_CONFIG):
                self.assertEqual(
                    jira_migrate.main(
                        ["apply", "--plan", str(plan_path), "--dry-run"]
                    ),
                    0,
                )

    def test_errors_return_exit_code_one(self) -> None:
        self.assertEqual(jira_migrate.main(["apply", "--plan", "/nope/plan.yaml"]), 1)


if __name__ == "__main__":
    unittest.main()
