from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import issue_plan
from epic_classifier import Assignment, Epic
from worklog_parser import WorkItem

EPICS = (
    Epic("DC-592", "[Data] 데이터 파이프라인 구축작업"),
    Epic("DC-974", "[AI]AI engineering 작업"),
)


def item(summary: str, project: str = "data-orchestrator", day: int = 19) -> WorkItem:
    return WorkItem(summary=summary, project=project, source_date=date(2026, 8, day))


def assign(summary: str, project: str = "data-orchestrator", epic: str = "DC-592", day: int = 19):
    return Assignment(item=item(summary, project, day), epic_key=epic)


class BuildPlanTests(unittest.TestCase):
    def build(self, tasks=(), subtasks=(), min_group=2):
        return issue_plan.build_plan(
            standalone=list(tasks),
            grouped=list(subtasks),
            epics=EPICS,
            project_key="DC",
            start=date(2026, 8, 19),
            end=date(2026, 8, 19),
            min_group=min_group,
        )

    def test_standalone_items_become_tasks_carrying_their_key(self) -> None:
        entry = assign("TAS 마트 신규 구축")

        plan = self.build(tasks=[entry])

        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].key, entry.item.key())
        self.assertEqual(plan.tasks[0].subtasks, ())
        self.assertEqual(plan.tasks[0].summary, "[Data] TAS 마트 신규 구축")

    def test_grouped_items_become_one_task_with_subtasks(self) -> None:
        entries = [assign(f"작업 {n}") for n in range(3)]

        plan = self.build(subtasks=entries)

        self.assertEqual(len(plan.tasks), 1)
        parent = plan.tasks[0]
        self.assertIsNone(parent.key)
        self.assertEqual(len(parent.subtasks), 3)
        self.assertTrue(parent.summary.startswith("[Data] "))
        self.assertIn("data-orchestrator", parent.summary)

    def test_subtask_summaries_carry_the_epic_prefix(self) -> None:
        plan = self.build(subtasks=[assign("작업 하나"), assign("작업 둘")])

        self.assertEqual(
            [s.summary for s in plan.tasks[0].subtasks],
            ["[Data] 작업 하나", "[Data] 작업 둘"],
        )

    def test_lone_grouped_item_is_promoted_to_a_task(self) -> None:
        plan = self.build(subtasks=[assign("혼자 남은 작업")])

        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].subtasks, ())
        self.assertIsNotNone(plan.tasks[0].key)

    def test_different_epics_do_not_share_a_parent(self) -> None:
        entries = [assign(f"d{n}", epic="DC-592") for n in range(2)]
        entries += [assign(f"a{n}", "llm-gateway", "DC-974") for n in range(2)]

        plan = self.build(subtasks=entries)

        self.assertEqual(len(plan.tasks), 2)
        prefixes = sorted(t.summary.split("]")[0] + "]" for t in plan.tasks)
        self.assertEqual(prefixes, ["[AI]", "[Data]"])

    def test_parent_summary_is_short_and_stable_across_dates(self) -> None:
        """The period lives in the date fields, not in the name, so a resumed
        run rebuilds the same parent name and reuses the recorded issue."""
        wide = self.build(subtasks=[assign(f"작업 {n}", day=17 + n) for n in range(3)])
        narrow = self.build(subtasks=[assign(f"작업 {n}") for n in range(3)])

        self.assertEqual(wide.tasks[0].summary, narrow.tasks[0].summary)
        self.assertEqual(wide.tasks[0].summary, "[Data] data-orchestrator 작업")

    def test_min_group_of_one_groups_even_a_single_item(self) -> None:
        plan = self.build(subtasks=[assign("혼자")], min_group=1)

        self.assertEqual(len(plan.tasks[0].subtasks), 1)


class SerialisationTests(unittest.TestCase):
    def plan(self) -> issue_plan.IssuePlan:
        return issue_plan.build_plan(
            standalone=[assign("독립 작업")],
            grouped=[assign(f"묶음 {n}") for n in range(2)],
            epics=EPICS,
            project_key="DC",
            start=date(2026, 8, 19),
            end=date(2026, 8, 19),
            min_group=2,
        )

    def test_round_trips_through_yaml(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            issue_plan.save(self.plan(), path)
            restored = issue_plan.load(path)

        self.assertEqual(restored, self.plan())

    def test_yaml_is_human_editable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            issue_plan.save(self.plan(), path)
            text = path.read_text(encoding="utf-8")

        self.assertIn("tasks:", text)
        self.assertIn("subtasks:", text)
        self.assertIn("epic: DC-592", text)
        self.assertIn("독립 작업", text)

    def test_moving_a_subtask_between_tasks_is_respected(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            issue_plan.save(self.plan(), path)

            text = path.read_text(encoding="utf-8")
            restored = issue_plan.load(path)
            moved_key = restored.tasks[-1].subtasks[0].key
            path.write_text(text, encoding="utf-8")

            edited = issue_plan.load(path)

        self.assertIn(moved_key, [s.key for task in edited.tasks for s in task.subtasks])

    def test_rejects_a_plan_with_a_duplicate_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            path.write_text(
                "version: 1\nproject: DC\nstart: 2026-08-19\nend: 2026-08-19\n"
                "tasks:\n"
                "  - summary: A\n    epic: DC-592\n    subtasks:\n"
                "      - {key: dup, summary: X}\n"
                "  - summary: B\n    epic: DC-592\n    subtasks:\n"
                "      - {key: dup, summary: Y}\n",
                encoding="utf-8",
            )
            with self.assertRaises(issue_plan.PlanError) as caught:
                issue_plan.load(path)

        self.assertIn("dup", str(caught.exception))

    def test_rejects_a_task_without_an_epic(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            path.write_text(
                "version: 1\nproject: DC\nstart: 2026-08-19\nend: 2026-08-19\n"
                "tasks:\n  - summary: A\n",
                encoding="utf-8",
            )
            with self.assertRaises(issue_plan.PlanError):
                issue_plan.load(path)

    def test_rejects_a_task_that_is_neither_backed_nor_a_parent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plan.yaml"
            path.write_text(
                "version: 1\nproject: DC\nstart: 2026-08-19\nend: 2026-08-19\n"
                "tasks:\n  - summary: A\n    epic: DC-592\n",
                encoding="utf-8",
            )
            with self.assertRaises(issue_plan.PlanError) as caught:
                issue_plan.load(path)

        self.assertIn("key", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
