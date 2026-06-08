import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from utils.mail_utils import (
    build_email_subject,
    build_mail_settings,
    build_resend_payload,
    parse_email_recipients,
    send_file_via_email,
)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        pass

    def read(self) -> bytes:
        return b'{"id":"email-id"}'


class MailUtilsTests(TestCase):
    def test_parse_email_recipients_accepts_common_list_formats(self) -> None:
        recipients = parse_email_recipients(
            [
                "one@example.com,two@example.com",
                "three@example.com;four@example.com",
                '["five@example.com", "six@example.com"]',
            ]
        )

        self.assertEqual(
            recipients,
            [
                "one@example.com",
                "two@example.com",
                "three@example.com",
                "four@example.com",
                "five@example.com",
                "six@example.com",
            ],
        )

    def test_build_mail_settings_reads_required_env_values(self) -> None:
        settings = build_mail_settings(
            {
                "EMAIL_TO": "one@example.com,two@example.com",
                "EMAIL_FROM": "Reports <reports@example.com>",
                "RESEND_API_KEY": "re_123",
            },
            "20260604",
        )

        self.assertEqual(settings.recipients, ["one@example.com", "two@example.com"])
        self.assertEqual(settings.sender, "Reports <reports@example.com>")
        self.assertEqual(settings.api_key, "re_123")
        self.assertEqual(settings.subject, "20260604 trade report")

    def test_build_email_subject_uses_execution_date(self) -> None:
        subject = build_email_subject("20260604")

        self.assertEqual(subject, "20260604 trade report")

    def test_build_mail_settings_requires_resend_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "RESEND_API_KEY"):
            build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@example.com",
                },
                "20260604",
            )

    def test_build_resend_payload_attaches_report(self) -> None:
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")

            settings = build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@example.com",
                    "RESEND_API_KEY": "re_123",
                },
                "20260604",
            )
            payload = build_resend_payload(report_path, settings)

        self.assertEqual(payload["from"], "sender@example.com")
        self.assertEqual(payload["to"], ["recipient@example.com"])
        self.assertEqual(payload["subject"], "20260604 trade report")
        self.assertEqual(payload["attachments"][0]["filename"], "report.xlsx")
        self.assertEqual(payload["attachments"][0]["content"], "cmVwb3J0")

    @patch("utils.mail_utils.urllib.request.urlopen", return_value=FakeResponse())
    def test_send_file_via_email_posts_to_resend(self, urlopen) -> None:
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")

            settings = build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@example.com",
                    "RESEND_API_KEY": "re_123",
                },
                "20260604",
            )
            send_file_via_email(report_path, settings, {})

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer re_123")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["attachments"][0]["content"], "cmVwb3J0")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30)
