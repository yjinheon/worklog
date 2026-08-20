from __future__ import annotations

import unittest
from datetime import date

import work_triage
from worklog_parser import WorkItem


def item(summary: str, project: str = "cdp-pipeline", **overrides) -> WorkItem:
    values = {"summary": summary, "project": project, "source_date": date(2026, 8, 18)}
    values.update(overrides)
    return WorkItem(**values)  # type: ignore[arg-type]


class PromptTests(unittest.TestCase):
    def test_prompt_lists_items_with_keys_and_demands_json(self) -> None:
        items = [item("TAS 캠페인 마트 신규 구축"), item("add gemini argparser")]

        prompt = work_triage.build_prompt(items)

        self.assertIn(items[0].key(), prompt)
        self.assertIn("TAS 캠페인 마트 신규 구축", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn("subtask", prompt)
        self.assertIn("기본", prompt)


class ResponseParsingTests(unittest.TestCase):
    def test_parses_json_array_wrapped_in_noise(self) -> None:
        noisy = (
            'hook noise\n'
            '[{"key":"abc","level":"task"},{"key":"def","level":"skip"},'
            '{"key":"ghi","level":"subtask"}]\nbye'
        )

        self.assertEqual(
            work_triage.parse_response(noisy),
            {"abc": "task", "def": "skip", "ghi": "subtask"},
        )

    def test_ignores_malformed_output(self) -> None:
        self.assertEqual(work_triage.parse_response("설명만 있음"), {})

    def test_ignores_unknown_levels(self) -> None:
        self.assertEqual(
            work_triage.parse_response('[{"key":"abc","level":"epic"}]'), {}
        )


class TriageTests(unittest.TestCase):
    """Sub-task is the default level; standalone Task is the exception."""

    def setUp(self) -> None:
        self.big = item("TAS 캠페인 마트 신규 구축", tags=("feat",))
        self.mid = item("campaign_active 플래그 추가", tags=("feat",))
        self.small = item("add gemini argparser", tags=("feat",))

    def verdicts(self, mapping: dict[str, str]):
        def llm(items):
            return mapping
        return llm

    def test_splits_items_across_the_three_levels(self) -> None:
        result = work_triage.triage(
            [self.big, self.mid, self.small],
            llm=self.verdicts(
                {
                    self.big.key(): "task",
                    self.mid.key(): "subtask",
                    self.small.key(): "skip",
                }
            ),
        )

        self.assertEqual([i.summary for i in result.tasks], [self.big.summary])
        self.assertEqual([i.summary for i in result.subtasks], [self.mid.summary])
        self.assertEqual([i.summary for i in result.minor], [self.small.summary])

    def test_unjudged_items_default_to_subtask(self) -> None:
        result = work_triage.triage([self.big, self.small], llm=self.verdicts({}))

        self.assertEqual(len(result.subtasks), 2)
        self.assertEqual(result.tasks, [])
        self.assertEqual(result.minor, [])

    def test_llm_failure_defaults_everything_to_subtask(self) -> None:
        def broken(items):
            raise RuntimeError("backend down")

        result = work_triage.triage([self.big, self.small], llm=broken)

        self.assertEqual(len(result.subtasks), 2)
        self.assertEqual(result.minor, [])

    def test_missing_llm_defaults_everything_to_subtask(self) -> None:
        result = work_triage.triage([self.big, self.small], llm=None)

        self.assertEqual(len(result.subtasks), 2)

    def test_empty_input_makes_no_call(self) -> None:
        calls: list[object] = []

        def llm(items):
            calls.append(items)
            return {}

        result = work_triage.triage([], llm=llm)

        self.assertEqual(result.subtasks, [])
        self.assertEqual(calls, [])

    def test_batches_large_inputs(self) -> None:
        items = [item(f"작업 {n}", tags=("feat",)) for n in range(work_triage.BATCH_SIZE + 5)]
        batches: list[int] = []

        def llm(chunk):
            batches.append(len(chunk))
            return {i.key(): "subtask" for i in chunk}

        result = work_triage.triage(items, llm=llm)

        self.assertEqual(len(result.subtasks), len(items))
        self.assertEqual(batches, [work_triage.BATCH_SIZE, 5])

    def test_one_failing_batch_does_not_drop_the_others(self) -> None:
        items = [item(f"작업 {n}", tags=("feat",)) for n in range(work_triage.BATCH_SIZE + 2)]

        def llm(chunk):
            if len(chunk) == work_triage.BATCH_SIZE:
                raise RuntimeError("boom")
            return {i.key(): "skip" for i in chunk}

        result = work_triage.triage(items, llm=llm)

        self.assertEqual(len(result.minor), 2)
        self.assertEqual(len(result.subtasks), work_triage.BATCH_SIZE)


if __name__ == "__main__":
    unittest.main()
