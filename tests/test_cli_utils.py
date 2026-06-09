from io import StringIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from utils.cli_utils import main, parse_args


class FakeLogger:
    def debug(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass


class CliUtilsTests(TestCase):
    def test_sendmail_flag_is_supported(self) -> None:
        args = parse_args(["20260604", "--sendmail"])

        self.assertTrue(args.sendmail)
        self.assertEqual(args.execution_date, "20260604")

    def test_removed_email_flags_are_not_supported(self) -> None:
        with patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["20260604", "--email-to", "recipient@example.com"])

    def test_credentials_file_flag_is_not_supported(self) -> None:
        with patch("sys.stderr", StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["20260604", "--credentials", "exports.sh"])

    @patch.dict("utils.cli_utils.os.environ", {}, clear=True)
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    @patch("utils.cli_utils.build_s3_client")
    def test_sendmail_validates_mail_env_before_s3(self, build_s3_client, _logger) -> None:
        result = main(["20260604", "--sendmail"])

        self.assertEqual(result, 1)
        build_s3_client.assert_not_called()

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "EMAIL_TO": "recipient@example.com",
            "EMAIL_FROM": "sender@gmail.com",
            "GMAIL_APP_PASSWORD": "abcdefghijklmnop",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_file_via_email", side_effect=OSError("Network is unreachable"))
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(warnings=[], downloaded_production_files=0),
    )
    @patch("utils.cli_utils.resolve_production_base_prefix", return_value="base-prefix")
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_sendmail_failure_returns_error(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_production_base_prefix,
        _download_bot_artifacts,
        _build_report_data,
        _write_report,
        _send_file_via_email,
    ) -> None:
        result = main(["20260604", "--sendmail"])

        self.assertEqual(result, 1)
