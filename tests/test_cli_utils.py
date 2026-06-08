from io import StringIO
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
