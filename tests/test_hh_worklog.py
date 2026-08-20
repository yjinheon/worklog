from __future__ import annotations

import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import hh_worklog


class HhWorklogTests(unittest.TestCase):
    def test_build_prompt_uses_raw_markdown_spec_and_output_rules(self) -> None:
        raw = "# Worklog raw 2026-05-07\n\n## repo (1 commits)\n\n```\nabc123 fix auth\n```"
        spec = "1. 오늘 발생한 이슈나 작업의 배경은 무엇인가요?\n\n2. 결과는 어떻고, 다음에 무엇을 개선해야 하나요?"

        prompt = hh_worklog.build_prompt(raw, spec)

        self.assertIn(raw, prompt)
        self.assertIn(spec, prompt)
        self.assertIn("최종 출력은 반드시 아래 형식만 사용", prompt)
        self.assertIn("[project] [tag, tag] 업무 요약", prompt)
        self.assertIn("간결한 한국어 명사형", prompt)
        self.assertIn("hh_spec 질문 제목은 출력하지 않음", prompt)

    def test_build_prompt_requires_daily_structured_output_with_meeting_placeholder(
        self,
    ) -> None:
        raw = "# Worklog raw 2026-06-22\n\n## llm-gateway (1 commits)"
        spec = "1. 질문"

        prompt = hh_worklog.build_prompt(raw, spec, output_date="2026-06-22")

        self.assertIn("2026-06-22", prompt)
        self.assertIn("[project] [tag, tag] 업무 요약", prompt)
        self.assertIn("[회의] [meeting] 회의명/논의 내용/후속 조치 작성 필요", prompt)
        self.assertIn("최종 출력은 반드시 아래 형식만 사용", prompt)

    def test_build_prompt_documents_the_issue_link(self) -> None:
        prompt = hh_worklog.build_prompt("# raw", "1. 질문")

        self.assertIn("[DC-123](https://humuson.atlassian.net/browse/DC-123)", prompt)
        self.assertIn("근거가 없으면 생략", prompt)
        self.assertIn("jira_create.py", prompt)

    def test_build_prompt_uses_the_configured_base_url_for_the_link(self) -> None:
        prompt = hh_worklog.build_prompt(
            "# raw", "1. 질문", {"JIRA_BASE_URL": "https://acme.atlassian.net"}
        )

        self.assertIn("https://acme.atlassian.net/browse/DC-123", prompt)

    def test_build_prompt_demands_short_noun_form_summaries(self) -> None:
        prompt = hh_worklog.build_prompt("# raw", "1. 질문")

        self.assertIn("40자", prompt)
        self.assertIn("명사형", prompt)
        self.assertIn("나열하지", prompt)

    def test_build_prompt_has_no_corrupted_characters(self) -> None:
        prompt = hh_worklog.build_prompt("# raw", "1. 질문")

        self.assertIn("마지막 폴더명", prompt)
        self.assertNotIn("폴\ufffd더명", prompt)
        self.assertNotIn("다륙\ufffd", prompt)

    def test_trims_preamble_before_the_date_line(self) -> None:
        noisy = (
            "⚠️ 알림: 메모리 저장 실패\n\n"
            "참고로 커밋이 중복되어 한 곳으로 정리했습니다.\n\n"
            "2026-08-18\n\n이슈사항\n\n- 배경\n"
        )

        cleaned = hh_worklog.trim_to_date_line(noisy, "2026-08-18")

        self.assertTrue(cleaned.startswith("2026-08-18"))
        self.assertNotIn("메모리 저장 실패", cleaned)
        self.assertIn("이슈사항", cleaned)

    def test_trim_leaves_clean_output_untouched(self) -> None:
        clean = "2026-08-18\n\n이슈사항\n\n- 배경\n"

        self.assertEqual(hh_worklog.trim_to_date_line(clean, "2026-08-18"), clean)

    def test_trim_keeps_text_when_the_date_line_is_absent(self) -> None:
        text = "설명만 있는 출력\n"

        self.assertEqual(hh_worklog.trim_to_date_line(text, "2026-08-18"), text)

    def test_trim_is_a_no_op_without_a_known_date(self) -> None:
        text = "머리말\n\n2026-08-18\n"

        self.assertEqual(hh_worklog.trim_to_date_line(text, None), text)

    def test_build_prompt_includes_config_context_when_provided(self) -> None:
        raw = "# Worklog raw 2026-05-07\n\n## data-pipeline (1 commits)"
        spec = "1. 오늘 발생한 이슈나 작업의 배경은 무엇인가요?"
        config = {
            "project_groups": {
                "data-engineering": {
                    "description": "Data pipelines and warehouse work",
                },
            },
            "repos": [
                {
                    "path": "~/workspace/data-pipeline",
                    "description": "Batch and streaming data pipeline implementation",
                    "group": "data-engineering",
                },
            ],
            "task_tags": ["feat", "fix"],
            "system_tags": ["clickhouse", "dagster"],
        }

        prompt = hh_worklog.build_prompt(raw, spec, config)

        self.assertIn("[프로젝트/태그 설정]", prompt)
        self.assertIn("data-engineering: Data pipelines and warehouse work", prompt)
        self.assertIn(
            "data-pipeline (description=Batch and streaming data pipeline implementation; group=data-engineering)",
            prompt,
        )
        self.assertIn("[task_tags] feat, fix", prompt)
        self.assertIn("[system_tags] clickhouse, dagster", prompt)

    def test_main_passes_config_yaml_context_to_prompt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "worklog.raw.md"
            spec_path = Path(tmpdir) / "hh_spec.md"
            config_path = Path(tmpdir) / "config.yaml"
            raw_path.write_text("# raw", encoding="utf-8")
            spec_path.write_text("1. 질문", encoding="utf-8")
            config_path.write_text(
                "\n".join(
                    [
                        "version: 3",
                        "repos:",
                        "  - path: ~/workspace/data-pipeline",
                        "    description: Batch data pipelines",
                        "task_tags: [feat, fix]",
                    ]
                ),
                encoding="utf-8",
            )
            prompts: list[str] = []

            def fake_run_llm(
                prompt: str,
                *,
                backend: str,
                work_dir: Path,
                timeout: int,
            ) -> tuple[str | None, str]:
                prompts.append(prompt)
                return "- 답변", "claude"

            with patch.object(hh_worklog, "run_llm", fake_run_llm):
                with patch("sys.stdout") as stdout:
                    rc = hh_worklog.main(
                        [
                            "hh_worklog.py",
                            str(raw_path),
                            "--spec",
                            str(spec_path),
                            "--config",
                            str(config_path),
                        ]
                    )

        self.assertEqual(rc, 0)
        self.assertIn("data-pipeline", prompts[0])
        self.assertIn("Batch data pipelines", prompts[0])
        stdout.write.assert_called_once_with("- 답변\n")

    def test_run_llm_tries_supported_backends_in_auto_mode(self) -> None:
        calls: list[str] = []

        def fake_claude(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
            calls.append("claude")
            return None

        def fake_gemini(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
            calls.append("gemini")
            return None

        def fake_kimi(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
            calls.append("kimi")
            return None

        def fake_codex(prompt: str, *, work_dir: Path, timeout: int) -> str | None:
            calls.append("codex")
            return "answer"

        with (
            patch.object(hh_worklog, "call_claude", fake_claude),
            patch.object(hh_worklog, "call_gemini", fake_gemini),
            patch.object(hh_worklog, "call_kimi", fake_kimi),
            patch.object(hh_worklog, "call_codex", fake_codex),
        ):
            result = hh_worklog.run_llm(
                "prompt",
                backend="auto",
                work_dir=Path("/tmp/worklog-cwd"),
                timeout=10,
            )

        self.assertEqual(result, ("answer", "codex"))
        self.assertEqual(calls, ["claude", "gemini", "kimi", "codex"])

    def test_run_llm_supports_explicit_kimi_and_codex_backends(self) -> None:
        for backend in ("kimi", "codex"):
            calls: list[str] = []

            with (
                patch.object(hh_worklog, "call_claude") as call_claude,
                patch.object(hh_worklog, "call_gemini") as call_gemini,
                patch.object(hh_worklog, "call_kimi") as call_kimi,
                patch.object(hh_worklog, "call_codex") as call_codex,
            ):
                call_kimi.side_effect = lambda *args, **kwargs: (
                    calls.append("kimi") or "kimi-answer"
                )
                call_codex.side_effect = lambda *args, **kwargs: (
                    calls.append("codex") or "codex-answer"
                )

                result = hh_worklog.run_llm(
                    "prompt",
                    backend=backend,
                    work_dir=Path("/tmp/worklog-cwd"),
                    timeout=10,
                )

            self.assertEqual(result, (f"{backend}-answer", backend))
            self.assertEqual(calls, [backend])
            call_claude.assert_not_called()
            call_gemini.assert_not_called()

    def test_main_prints_llm_answer_to_stdout(self) -> None:
        with TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "worklog.raw.md"
            spec_path = Path(tmpdir) / "hh_spec.md"
            raw_path.write_text("# raw", encoding="utf-8")
            spec_path.write_text("1. 질문", encoding="utf-8")

            with patch.object(hh_worklog, "run_llm", return_value=("- 답변", "claude")):
                with patch("sys.stdout") as stdout:
                    rc = hh_worklog.main(
                        [
                            "hh_worklog.py",
                            str(raw_path),
                            "--spec",
                            str(spec_path),
                            "--backend",
                            "claude",
                        ]
                    )

        self.assertEqual(rc, 0)
        stdout.write.assert_called_once_with("- 답변\n")

    def test_main_accepts_date_and_resolves_tmp_raw_path(self) -> None:
        spec_path = Path("/tmp/hh-spec-for-date-test.md")
        raw_path = Path("/tmp/worklog-2026-05-07.raw.md")
        spec_path.write_text("1. 질문", encoding="utf-8")
        raw_path.write_text("# raw from date", encoding="utf-8")
        prompts: list[str] = []

        def fake_run_llm(
            prompt: str,
            *,
            backend: str,
            work_dir: Path,
            timeout: int,
        ) -> tuple[str | None, str]:
            prompts.append(prompt)
            return "- 답변", "claude"

        try:
            with patch.object(hh_worklog, "run_llm", fake_run_llm):
                with patch("sys.stdout") as stdout:
                    rc = hh_worklog.main(
                        [
                            "hh_worklog.py",
                            "2026-05-07",
                            "--spec",
                            str(spec_path),
                        ]
                    )
        finally:
            spec_path.unlink(missing_ok=True)
            raw_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertIn("# raw from date", prompts[0])
        self.assertIn("첫 줄은 날짜만 작성: 2026-05-07", prompts[0])
        stdout.write.assert_called_once_with("- 답변\n")

    def test_main_generates_missing_raw_file_for_date_input(self) -> None:
        target_date = "2099-12-31"
        spec_path = Path("/tmp/hh-spec-for-generate-test.md")
        raw_path = Path(f"/tmp/worklog-{target_date}.raw.md")
        spec_path.write_text("1. 질문", encoding="utf-8")
        raw_path.unlink(missing_ok=True)
        prompts: list[str] = []
        commands: list[list[str]] = []

        def fake_run(
            cmd: list[str],
            *,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(cmd)
            raw_path.write_text("# generated raw", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        def fake_run_llm(
            prompt: str,
            *,
            backend: str,
            work_dir: Path,
            timeout: int,
        ) -> tuple[str | None, str]:
            prompts.append(prompt)
            return "- 답변", "claude"

        try:
            with (
                patch("subprocess.run", fake_run),
                patch.object(hh_worklog, "run_llm", fake_run_llm),
                patch("sys.stdout") as stdout,
            ):
                rc = hh_worklog.main(
                    [
                        "hh_worklog.py",
                        target_date,
                        "--spec",
                        str(spec_path),
                    ]
                )
        finally:
            spec_path.unlink(missing_ok=True)
            raw_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertEqual(commands, [[str(hh_worklog.DAILY_SCRIPT), target_date]])
        self.assertIn("# generated raw", prompts[0])
        stdout.write.assert_called_once_with("- 답변\n")


if __name__ == "__main__":
    unittest.main()
