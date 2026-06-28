import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs

from utils.slack_utils import (
    SlackApiError,
    build_slack_settings,
    send_file_via_slack,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status


class SlackUtilsTests(TestCase):
    def test_build_slack_settings_reads_env_values(self) -> None:
        settings = build_slack_settings(
            {
                "SLACK_BOT_TOKEN": " xoxb-test-token ",
                "SLACK_CHANNEL_ID": " C123 ",
                "SLACK_REPORT_INITIAL_COMMENT": "Production report",
                "SLACK_REPORT_THREAD_TS": "1712345678.123456",
                "SLACK_REPORT_UPLOAD_STRICT": "false",
                "SLACK_REPORT_TIMEOUT_SECONDS": "45",
            },
            "20260604",
        )

        self.assertEqual(settings.token, "xoxb-test-token")
        self.assertEqual(settings.channel_id, "C123")
        self.assertEqual(settings.title, "20260604 trade report")
        self.assertEqual(settings.initial_comment, "Production report")
        self.assertEqual(settings.thread_ts, "1712345678.123456")
        self.assertFalse(settings.upload_strict)
        self.assertEqual(settings.timeout, 45)

    def test_build_slack_settings_requires_token_and_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "SLACK_BOT_TOKEN"):
            build_slack_settings({}, "20260604")

    def test_build_slack_settings_ignores_thread_placeholder(self) -> None:
        settings = build_slack_settings(
            {
                "SLACK_BOT_TOKEN": "xoxb-test-token",
                "SLACK_CHANNEL_ID": "C123",
                "SLACK_REPORT_THREAD_TS": "parent-message-ts",
            },
            "20260604",
        )

        self.assertIsNone(settings.thread_ts)

    def test_build_slack_settings_rejects_invalid_thread_ts(self) -> None:
        with self.assertRaisesRegex(ValueError, "SLACK_REPORT_THREAD_TS"):
            build_slack_settings(
                {
                    "SLACK_BOT_TOKEN": "xoxb-test-token",
                    "SLACK_CHANNEL_ID": "C123",
                    "SLACK_REPORT_THREAD_TS": "not-a-ts",
                },
                "20260604",
            )

    @patch("utils.slack_utils.urlopen")
    def test_send_file_via_slack_uses_external_upload_flow(self, urlopen) -> None:
        urlopen.side_effect = [
            FakeResponse(
                json.dumps(
                    {
                        "ok": True,
                        "upload_url": "https://files.slack.com/upload/v1/ABC",
                        "file_id": "F123",
                    }
                ).encode("utf-8")
            ),
            FakeResponse(b""),
            FakeResponse(
                json.dumps(
                    {
                        "ok": True,
                        "files": [{"id": "F123", "title": "20260604 trade report"}],
                    }
                ).encode("utf-8")
            ),
        ]

        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")
            settings = build_slack_settings(
                {
                    "SLACK_BOT_TOKEN": "xoxb-test-token",
                    "SLACK_CHANNEL_ID": "C123",
                    "SLACK_REPORT_INITIAL_COMMENT": "Production report",
                    "SLACK_REPORT_THREAD_TS": "1712345678.123456",
                },
                "20260604",
            )

            result = send_file_via_slack(report_path, settings)

        self.assertTrue(result["ok"])
        self.assertEqual(urlopen.call_count, 3)

        get_url_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(
            get_url_request.full_url,
            "https://slack.com/api/files.getUploadURLExternal",
        )
        self.assertEqual(
            get_url_request.get_header("Authorization"),
            "Bearer xoxb-test-token",
        )
        self.assertEqual(
            get_url_request.get_header("Content-type"),
            "application/x-www-form-urlencoded; charset=utf-8",
        )
        self.assertEqual(
            parse_qs(get_url_request.data.decode("utf-8")),
            {"filename": ["report.xlsx"], "length": ["6"]},
        )

        upload_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(
            upload_request.full_url,
            "https://files.slack.com/upload/v1/ABC",
        )
        self.assertEqual(upload_request.data, b"report")
        self.assertIsNone(upload_request.get_header("Authorization"))

        complete_request = urlopen.call_args_list[2].args[0]
        self.assertEqual(
            complete_request.full_url,
            "https://slack.com/api/files.completeUploadExternal",
        )
        complete_payload = parse_qs(complete_request.data.decode("utf-8"))
        self.assertEqual(complete_payload["channel_id"], ["C123"])
        self.assertEqual(complete_payload["initial_comment"], ["Production report"])
        self.assertEqual(complete_payload["thread_ts"], ["1712345678.123456"])
        self.assertEqual(
            json.loads(complete_payload["files"][0]),
            [{"id": "F123", "title": "20260604 trade report"}],
        )

    @patch("utils.slack_utils.urlopen")
    def test_send_file_via_slack_raises_on_slack_error_with_details(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(
            json.dumps(
                {
                    "ok": False,
                    "error": "invalid_arguments",
                    "response_metadata": {
                        "messages": ["[ERROR] length must be greater than 0"]
                    },
                }
            ).encode("utf-8")
        )

        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")
            settings = build_slack_settings(
                {
                    "SLACK_BOT_TOKEN": "xoxb-test-token",
                    "SLACK_CHANNEL_ID": "C123",
                },
                "20260604",
            )

            with self.assertRaisesRegex(SlackApiError, "length must be greater than 0"):
                send_file_via_slack(report_path, settings)
