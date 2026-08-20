from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import jira_client


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def write_config(root: Path, body: str) -> Path:
    path = root / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class JiraConfigTests(unittest.TestCase):
    def test_load_prefers_environment_token_and_strips_trailing_slash(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = write_config(
                Path(tmpdir),
                "JIRA_BASE_URL: https://example.atlassian.net/\n"
                "JIRA_EMAIL: worker@example.com\n"
                "CONFLUENCE_TOKEN: legacy-token\n",
            )
            with patch.dict("os.environ", {"JIRA_API_TOKEN": "env-token"}, clear=True):
                config_obj = jira_client.JiraConfig.load(config)

        self.assertEqual(config_obj.base_url, "https://example.atlassian.net")
        self.assertEqual(config_obj.token, "env-token")
        self.assertFalse(config_obj.uses_legacy_token)

    def test_load_falls_back_to_legacy_token_when_allowed(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = write_config(
                Path(tmpdir),
                "CONFLUENCE_BASE_URL: https://example.atlassian.net\n"
                "CONFLUENCE_EMAIL: worker@example.com\n"
                "CONFLUENCE_TOKEN: legacy-token\n",
            )
            with patch.dict("os.environ", {}, clear=True):
                config_obj = jira_client.JiraConfig.load(config)

        self.assertEqual(config_obj.token, "legacy-token")
        self.assertTrue(config_obj.uses_legacy_token)

    def test_load_rejects_legacy_token_when_env_token_required(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config = write_config(
                Path(tmpdir),
                "CONFLUENCE_BASE_URL: https://example.atlassian.net\n"
                "CONFLUENCE_EMAIL: worker@example.com\n"
                "CONFLUENCE_TOKEN: legacy-token\n",
            )
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(jira_client.JiraError) as caught:
                    jira_client.JiraConfig.load(config, allow_legacy_token=False)

        self.assertIn("JIRA_API_TOKEN", str(caught.exception))
        self.assertNotIn("legacy-token", str(caught.exception))

    def test_load_reports_missing_file_and_missing_settings(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with self.assertRaises(jira_client.JiraError):
                jira_client.JiraConfig.load(root / "absent.yaml")

            config = write_config(root, "JIRA_EMAIL: worker@example.com\n")
            with patch.dict("os.environ", {"JIRA_API_TOKEN": "t"}, clear=True):
                with self.assertRaises(jira_client.JiraError) as caught:
                    jira_client.JiraConfig.load(config)

        self.assertIn("base URL", str(caught.exception))


class RequestTests(unittest.TestCase):
    def config(self) -> jira_client.JiraConfig:
        return jira_client.JiraConfig(
            base_url="https://example.atlassian.net",
            email="worker@example.com",
            token="secret-token",
        )

    def test_request_sends_json_body_with_basic_auth(self) -> None:
        with patch(
            "jira_client.urlopen", return_value=FakeResponse({"ok": True})
        ) as opened:
            result = jira_client.request(
                self.config(), "POST", "/rest/api/3/issue", {"fields": {"a": 1}}
            )

        self.assertEqual(result, {"ok": True})
        sent = opened.call_args.args[0]
        self.assertEqual(sent.full_url, "https://example.atlassian.net/rest/api/3/issue")
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(json.loads(sent.data), {"fields": {"a": 1}})
        self.assertTrue(sent.headers["Authorization"].startswith("Basic "))
        self.assertEqual(sent.headers["Content-type"], "application/json")

    def test_request_omits_body_for_get(self) -> None:
        with patch(
            "jira_client.urlopen", return_value=FakeResponse({"accountId": "abc"})
        ) as opened:
            result = jira_client.request(self.config(), "GET", "/rest/api/3/myself")

        self.assertEqual(result, {"accountId": "abc"})
        sent = opened.call_args.args[0]
        self.assertIsNone(sent.data)
        self.assertEqual(sent.get_method(), "GET")

    def test_request_wraps_transport_errors_without_leaking_token(self) -> None:
        with patch("jira_client.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(jira_client.JiraError) as caught:
                jira_client.request(self.config(), "GET", "/rest/api/3/myself")

        self.assertIn("offline", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_request_includes_http_status_and_body_snippet(self) -> None:
        error = HTTPError(
            "https://example.atlassian.net/rest/api/3/issue",
            400,
            "Bad Request",
            {},
            None,
        )
        error.read = lambda: b'{"errors":{"customfield_10015":"Field cannot be set"}}'
        with patch("jira_client.urlopen", side_effect=error):
            with self.assertRaises(jira_client.JiraError) as caught:
                jira_client.request(self.config(), "POST", "/rest/api/3/issue", {})

        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn("customfield_10015", message)

    def test_http_error_exposes_status_for_retry_decisions(self) -> None:
        error = HTTPError("https://example.atlassian.net/x", 429, "Too Many", {}, None)
        error.read = lambda: b"rate limited"
        with patch("jira_client.urlopen", side_effect=error):
            with self.assertRaises(jira_client.JiraError) as caught:
                jira_client.request(self.config(), "POST", "/rest/api/3/issue", {})

        self.assertEqual(caught.exception.status, 429)

    def test_transport_error_has_no_status(self) -> None:
        with patch("jira_client.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(jira_client.JiraError) as caught:
                jira_client.request(self.config(), "GET", "/rest/api/3/myself")

        self.assertIsNone(caught.exception.status)

    def test_request_rejects_non_json_payload(self) -> None:
        with patch("jira_client.urlopen", return_value=FakeResponse("not-a-mapping")):
            with self.assertRaises(jira_client.JiraError):
                jira_client.request(self.config(), "GET", "/rest/api/3/myself")


if __name__ == "__main__":
    unittest.main()
