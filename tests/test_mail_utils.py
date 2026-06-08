from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from utils.mail_utils import (
    build_email_subject,
    build_mail_settings,
    parse_email_recipients,
    send_file_via_email,
)


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
                "EMAIL_FROM": "sender@gmail.com",
                "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
            },
            "20260604",
        )

        self.assertEqual(settings.recipients, ["one@example.com", "two@example.com"])
        self.assertEqual(settings.sender, "sender@gmail.com")
        self.assertEqual(settings.app_password, "abcdefghijklmnop")
        self.assertEqual(settings.subject, "20260604 trade report")

    def test_build_email_subject_uses_execution_date(self) -> None:
        subject = build_email_subject("20260604")

        self.assertEqual(subject, "20260604 trade report")

    def test_send_file_via_email_requires_gmail_app_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "GMAIL_APP_PASSWORD"):
            build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@gmail.com",
                },
                "20260604",
            )

    @patch("utils.mail_utils.smtplib.SMTP")
    def test_send_file_via_email_uses_gmail_defaults(self, smtp_class) -> None:
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")

            settings = build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@gmail.com",
                    "GMAIL_APP_PASSWORD": "abcdefghijklmnop",
                },
                "20260604",
            )
            send_file_via_email(
                report_path,
                settings,
                {},
            )

        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
        smtp = smtp_class.return_value.__enter__.return_value
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")
        smtp.send_message.assert_called_once()

    @patch("utils.mail_utils.IPv4SMTPSSL")
    def test_send_file_via_email_can_force_ipv4_with_ssl(self, smtp_class) -> None:
        with TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.xlsx"
            report_path.write_bytes(b"report")

            settings = build_mail_settings(
                {
                    "EMAIL_TO": "recipient@example.com",
                    "EMAIL_FROM": "sender@gmail.com",
                    "GMAIL_APP_PASSWORD": "abcdefghijklmnop",
                },
                "20260604",
            )
            send_file_via_email(
                report_path,
                settings,
                {
                    "SMTP_FORCE_IPV4": "true",
                    "SMTP_USE_SSL": "true",
                    "SMTP_PORT": "465",
                },
            )

        smtp_class.assert_called_once()
        smtp = smtp_class.return_value.__enter__.return_value
        smtp.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")
        smtp.send_message.assert_called_once()
