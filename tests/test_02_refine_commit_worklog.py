from __future__ import annotations

import importlib
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

refine_worklog = importlib.import_module("02_refine_commit_worklog")


class RefineWorklogTests(unittest.TestCase):
    def test_subscription_env_trusts_workspace_for_headless_gemini(self) -> None:
        env = refine_worklog._subscription_env()

        self.assertEqual(env["GEMINI_CLI_TRUST_WORKSPACE"], "true")

    def test_subscription_env_does_not_force_claude_simple_mode(self) -> None:
        env = refine_worklog._subscription_env()

        self.assertNotIn("CLAUDE_CODE_SIMPLE", env)

    def test_subscription_env_removes_openai_api_key_for_codex_subscription(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}):
            env = refine_worklog._subscription_env()

        self.assertNotIn("OPENAI_API_KEY", env)

    def test_call_codex_uses_exec_and_reads_last_message_output(self) -> None:
        commands: list[list[str]] = []
        out_log = Path("/tmp/refine-codex.out")
        out_log.unlink(missing_ok=True)

        def fake_run(
            cmd: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
            input: str,
            capture_output: bool,
            text: bool,
            timeout: int,
        ) -> subprocess.CompletedProcess[str]:
            commands.append(cmd)
            out_log.write_text("structured answer\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="ignored stdout", stderr="")

        try:
            with (
                patch("shutil.which", return_value="/usr/local/bin/codex"),
                patch("subprocess.run", fake_run),
            ):
                result = refine_worklog.call_codex(
                    "prompt",
                    work_dir=Path("/tmp/worklog-cwd"),
                    timeout=10,
                )
        finally:
            out_log.unlink(missing_ok=True)

        self.assertEqual(result, "structured answer")
        self.assertEqual(commands[0][:2], ["codex", "exec"])
        self.assertIn("--skip-git-repo-check", commands[0])
        self.assertIn("--output-last-message", commands[0])
        self.assertNotIn("--ask-for-approval", commands[0])


if __name__ == "__main__":
    unittest.main()
