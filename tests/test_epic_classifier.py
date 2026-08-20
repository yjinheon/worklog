from __future__ import annotations

import unittest
from datetime import date

import epic_classifier
from worklog_parser import WorkItem

EPICS = (
    epic_classifier.Epic("DC-592", "[Data] 내부 데이터 파이프라인 구축작업"),
    epic_classifier.Epic("DC-829", "[AI] LLM 서빙용 LiteLLM Gateway 환경 구축"),
    epic_classifier.Epic("DC-901", "데이터  오케스트레이터 고도화 및 일원화 작업"),
)

PROJECT_GROUPS = {
    "cdp-pipeline": "data-engineering",
    "llm-gateway": "ai-engineering",
    "data-orchestrator": "data-engineering",
    "legacy-ops": "operations",
    "worklog": "worklog-system",
}

EPIC_MAP = {
    "data-engineering": "DC-592",
    "ai-engineering": "DC-829",
    "operations": "데이터 오케스트레이터 고도화 및 일원화 작업",
    "worklog-system": None,
}


def item(project: str, summary: str = "작업") -> WorkItem:
    return WorkItem(
        summary=summary,
        project=project,
        tags=("feat",),
        done=True,
        source_date=date(2026, 8, 12),
    )


class EpicMapResolutionTests(unittest.TestCase):
    def test_resolves_keys_names_and_exclusions(self) -> None:
        resolved = epic_classifier.resolve_epic_map(EPIC_MAP, EPICS)

        self.assertEqual(resolved["data-engineering"], "DC-592")
        self.assertEqual(resolved["operations"], "DC-901")
        self.assertIsNone(resolved["worklog-system"])

    def test_name_match_ignores_case_spacing_and_bracket_prefix(self) -> None:
        resolved = epic_classifier.resolve_epic_map(
            {"data-engineering": "내부 데이터   파이프라인"}, EPICS
        )
        self.assertEqual(resolved["data-engineering"], "DC-592")

    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(epic_classifier.EpicMapError) as caught:
            epic_classifier.resolve_epic_map({"operations": "DC-404"}, EPICS)
        self.assertIn("DC-404", str(caught.exception))

    def test_name_without_match_is_rejected(self) -> None:
        with self.assertRaises(epic_classifier.EpicMapError):
            epic_classifier.resolve_epic_map({"operations": "존재하지 않는 에픽"}, EPICS)

    def test_ambiguous_name_is_rejected_instead_of_guessing(self) -> None:
        with self.assertRaises(epic_classifier.EpicMapError) as caught:
            epic_classifier.resolve_epic_map({"operations": "구축"}, EPICS)
        self.assertIn("DC-592", str(caught.exception))
        self.assertIn("DC-829", str(caught.exception))


class ProjectGroupTests(unittest.TestCase):
    def test_reads_group_by_repo_folder_name(self) -> None:
        config = {
            "repos": [
                {"path": "~/workspace/cdp-pipeline", "group": "data-engineering"},
                {"path": "~/workspace/01.Project/humuson-cdp", "group": "data-engineering"},
                {"path": "~/workspace/no-group"},
            ]
        }
        groups = epic_classifier.project_groups_from_config(config)

        self.assertEqual(groups["cdp-pipeline"], "data-engineering")
        self.assertEqual(groups["humuson-cdp"], "data-engineering")
        self.assertNotIn("no-group", groups)


class ClassificationTests(unittest.TestCase):
    def classify(self, items, llm=None):
        return epic_classifier.classify(
            items,
            project_groups=PROJECT_GROUPS,
            epic_map=epic_classifier.resolve_epic_map(EPIC_MAP, EPICS),
            epics=EPICS,
            llm=llm,
        )

    def test_maps_deterministically_without_calling_llm(self) -> None:
        calls: list[object] = []

        def llm(items, epics):
            calls.append(items)
            return {}

        result = self.classify(
            [item("cdp-pipeline"), item("llm-gateway"), item("legacy-ops")],
            llm=llm,
        )

        self.assertEqual(
            [(entry.item.project, entry.epic_key) for entry in result.assigned],
            [
                ("cdp-pipeline", "DC-592"),
                ("llm-gateway", "DC-829"),
                ("legacy-ops", "DC-901"),
            ],
        )
        self.assertEqual(calls, [])

    def test_null_mapped_group_is_excluded(self) -> None:
        result = self.classify([item("worklog")])

        self.assertEqual(result.assigned, [])
        self.assertEqual([entry.project for entry in result.excluded], ["worklog"])
        self.assertEqual(result.unresolved, [])

    def test_unknown_project_falls_back_to_llm(self) -> None:
        unknown = item("brand-new-repo")

        def llm(items, epics):
            self.assertEqual([entry.project for entry in items], ["brand-new-repo"])
            return {items[0].key(): "DC-592"}

        result = self.classify([unknown], llm=llm)

        self.assertEqual(
            [(entry.item.project, entry.epic_key) for entry in result.assigned],
            [("brand-new-repo", "DC-592")],
        )

    def test_llm_abstention_leaves_item_unresolved(self) -> None:
        result = self.classify([item("brand-new-repo")], llm=lambda items, epics: {})

        self.assertEqual(result.assigned, [])
        self.assertEqual([entry.project for entry in result.unresolved], ["brand-new-repo"])

    def test_llm_answer_outside_epic_list_is_rejected(self) -> None:
        result = self.classify(
            [item("brand-new-repo")],
            llm=lambda items, epics: {items[0].key(): "DC-999"},
        )

        self.assertEqual(result.assigned, [])
        self.assertEqual(len(result.unresolved), 1)

    def test_llm_failure_does_not_abort_deterministic_results(self) -> None:
        def llm(items, epics):
            raise RuntimeError("backend unavailable")

        result = self.classify([item("cdp-pipeline"), item("brand-new-repo")], llm=llm)

        self.assertEqual([entry.item.project for entry in result.assigned], ["cdp-pipeline"])
        self.assertEqual([entry.project for entry in result.unresolved], ["brand-new-repo"])

    def test_missing_llm_leaves_unmapped_items_unresolved(self) -> None:
        result = self.classify([item("brand-new-repo")], llm=None)

        self.assertEqual([entry.project for entry in result.unresolved], ["brand-new-repo"])


class PromptTests(unittest.TestCase):
    def test_prompt_lists_epics_and_demands_json(self) -> None:
        prompt = epic_classifier.build_prompt([item("brand-new-repo", "요약")], EPICS)

        self.assertIn("DC-592", prompt)
        self.assertIn("[Data] 내부 데이터 파이프라인 구축작업", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn("요약", prompt)

    def test_parses_json_array_wrapped_in_noise(self) -> None:
        noisy = 'hook noise\n[{"key":"abc123","epic_key":"DC-592"}]\ntrailing note'

        self.assertEqual(
            epic_classifier.parse_llm_response(noisy), {"abc123": "DC-592"}
        )

    def test_parse_returns_empty_on_malformed_output(self) -> None:
        self.assertEqual(epic_classifier.parse_llm_response("설명만 있고 JSON 없음"), {})

    def test_parse_treats_null_epic_as_abstention(self) -> None:
        self.assertEqual(
            epic_classifier.parse_llm_response('[{"key":"abc","epic_key":null}]'), {}
        )


class ProjectOverrideTests(unittest.TestCase):
    """A project key in epic_map wins over the group its repo belongs to."""

    def classify(self, items, raw_map):
        return epic_classifier.classify(
            items,
            project_groups=PROJECT_GROUPS,
            epic_map=epic_classifier.resolve_epic_map(raw_map, EPICS),
            epics=EPICS,
            llm=None,
        )

    def test_project_entry_overrides_group_mapping(self) -> None:
        raw_map = dict(EPIC_MAP)
        raw_map["data-orchestrator"] = "데이터 오케스트레이터 고도화 및 일원화 작업"

        result = self.classify(
            [item("data-orchestrator"), item("cdp-pipeline")], raw_map
        )

        self.assertEqual(
            [(entry.item.project, entry.epic_key) for entry in result.assigned],
            [("data-orchestrator", "DC-901"), ("cdp-pipeline", "DC-592")],
        )

    def test_project_entry_can_exclude_a_single_project(self) -> None:
        raw_map = dict(EPIC_MAP)
        raw_map["cdp-pipeline"] = None

        result = self.classify(
            [item("cdp-pipeline"), item("llm-gateway")], raw_map
        )

        self.assertEqual([entry.project for entry in result.excluded], ["cdp-pipeline"])
        self.assertEqual(
            [entry.item.project for entry in result.assigned], ["llm-gateway"]
        )

    def test_group_still_applies_when_no_project_entry_exists(self) -> None:
        result = self.classify([item("data-orchestrator")], EPIC_MAP)

        self.assertEqual(result.assigned[0].epic_key, "DC-592")


class EpicMapValidationTests(unittest.TestCase):
    def test_accepts_group_and_project_keys(self) -> None:
        epic_classifier.validate_epic_map_keys(
            {"data-engineering": "DC-592", "cdp-pipeline": "DC-592"}, PROJECT_GROUPS
        )

    def test_rejects_key_that_is_neither_group_nor_project(self) -> None:
        with self.assertRaises(epic_classifier.EpicMapError) as caught:
            epic_classifier.validate_epic_map_keys(
                {"data-orchestrater": "DC-592"}, PROJECT_GROUPS
            )
        self.assertIn("data-orchestrater", str(caught.exception))

    def test_rejects_key_that_is_both_group_and_project(self) -> None:
        groups = dict(PROJECT_GROUPS, operations="data-engineering")

        with self.assertRaises(epic_classifier.EpicMapError) as caught:
            epic_classifier.validate_epic_map_keys({"operations": "DC-592"}, groups)
        self.assertIn("operations", str(caught.exception))


class DeclaredGroupTests(unittest.TestCase):
    """A group declared in config but not yet used by any repo is still valid."""

    CONFIG = {
        "project_groups": {
            "data-engineering": {"description": "..."},
            "operations": {"description": "..."},
        },
        "repos": [{"path": "~/workspace/cdp-pipeline", "group": "data-engineering"}],
    }

    def test_declared_groups_are_read_from_config(self) -> None:
        self.assertEqual(
            epic_classifier.declared_groups_from_config(self.CONFIG),
            {"data-engineering", "operations"},
        )

    def test_unused_declared_group_passes_validation(self) -> None:
        epic_classifier.validate_epic_map_keys(
            {"operations": "DC-901"},
            epic_classifier.project_groups_from_config(self.CONFIG),
            declared_groups=epic_classifier.declared_groups_from_config(self.CONFIG),
        )

    def test_typo_still_rejected_against_declared_groups(self) -> None:
        with self.assertRaises(epic_classifier.EpicMapError):
            epic_classifier.validate_epic_map_keys(
                {"operatoins": "DC-901"},
                epic_classifier.project_groups_from_config(self.CONFIG),
                declared_groups=epic_classifier.declared_groups_from_config(self.CONFIG),
            )


if __name__ == "__main__":
    unittest.main()
