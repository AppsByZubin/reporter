from io import StringIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from utils.cli_utils import BotProcessingTimeout, main, parse_args


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

    def test_slack_flag_is_supported(self) -> None:
        args = parse_args(["20260604", "--slack"])

        self.assertTrue(args.slack)
        self.assertFalse(args.sendmail)
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
        {"REPORTER_BOT_TIMEOUT_SECONDS": "not-a-number"},
        clear=True,
    )
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    @patch("utils.cli_utils.build_s3_client")
    def test_invalid_bot_timeout_returns_error_before_s3(
        self,
        build_s3_client,
        _logger,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 1)
        build_s3_client.assert_not_called()

    @patch.dict("utils.cli_utils.os.environ", {}, clear=True)
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    @patch("utils.cli_utils.build_s3_client")
    def test_slack_validates_env_before_s3(self, build_s3_client, _logger) -> None:
        result = main(["20260604", "--slack"])

        self.assertEqual(result, 1)
        build_s3_client.assert_not_called()

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "EMAIL_TO": "recipient@example.com",
            "EMAIL_FROM": "sender@gmail.com",
            "GMAIL_APP_PASSWORD": "abcdefghijklmnop",
            "UPSTOX_API_ACCESS_TOKEN": "upstox-token",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_file_via_email", side_effect=OSError("Network is unreachable"))
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=0,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_sendmail_failure_returns_error(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _download_bot_artifacts,
        _build_report_data,
        _write_report,
        _send_file_via_email,
    ) -> None:
        result = main(["20260604", "--sendmail"])

        self.assertEqual(result, 1)

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_ID": "C123",
            "UPSTOX_API_ACCESS_TOKEN": "upstox-token",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_file_via_slack", side_effect=OSError("Network is unreachable"))
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=0,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_slack_failure_returns_error_when_strict(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _download_bot_artifacts,
        _build_report_data,
        _write_report,
        _send_file_via_slack,
    ) -> None:
        result = main(["20260604", "--slack"])

        self.assertEqual(result, 1)

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_ID": "C123",
            "SLACK_REPORT_UPLOAD_STRICT": "false",
            "UPSTOX_API_ACCESS_TOKEN": "upstox-token",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_file_via_slack", side_effect=OSError("Network is unreachable"))
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=0,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_slack_failure_can_be_non_strict(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _download_bot_artifacts,
        _build_report_data,
        _write_report,
        send_file_via_slack,
    ) -> None:
        result = main(["20260604", "--slack"])

        self.assertEqual(result, 0)
        send_file_via_slack.assert_called_once()

    @patch.dict(
        "utils.cli_utils.os.environ",
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=0,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        side_effect=[
            SimpleNamespace(base_prefix="firebot-prefix", artifact_kind="production"),
            None,
        ],
    )
    @patch(
        "utils.cli_utils.candidate_artifact_prefixes",
        return_value=["missing-prefix/"],
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot", "trendobot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_missing_production_skips_bot_and_writes_report(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _candidate_artifact_prefixes,
        _resolve_artifact_prefix,
        download_bot_artifacts,
        _build_report_data,
        write_report,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 0)
        download_bot_artifacts.assert_called_once()
        self.assertEqual(download_bot_artifacts.call_args.args[3], "firebot")
        self.assertEqual(write_report.call_args.args[2], ["firebot", "trendobot"])

    @patch.dict(
        "utils.cli_utils.os.environ",
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        side_effect=[
            SimpleNamespace(
                warnings=[],
                artifact_kind="production",
                downloaded_artifact_files=1,
            ),
            SimpleNamespace(
                warnings=[],
                artifact_kind="production",
                downloaded_artifact_files=1,
            ),
        ],
    )
    @patch("utils.cli_utils.run_with_timeout")
    @patch(
        "utils.cli_utils.build_local_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=0,
            local_dir="downloads/20260604/trendobot",
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot", "trendobot", "titanbot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_timed_out_bot_is_skipped_and_next_bot_runs(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _build_local_bot_artifacts,
        run_with_timeout,
        download_bot_artifacts,
        _build_report_data,
        write_report,
    ) -> None:
        call_count = 0

        def maybe_timeout(_timeout_seconds, callback):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise BotProcessingTimeout()
            return callback()

        run_with_timeout.side_effect = maybe_timeout

        result = main(["20260604"])

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[3] for call in download_bot_artifacts.call_args_list],
            ["firebot", "titanbot"],
        )
        self.assertEqual(
            set(write_report.call_args.args[3]),
            {"firebot", "titanbot"},
        )

    @patch.dict(
        "utils.cli_utils.os.environ",
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch(
        "utils.cli_utils.build_report_data",
        side_effect=[
            ([{"trade_id": "fire"}], "fire observation"),
            ([{"trade_id": "trend"}], "trend observation"),
        ],
    )
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=1,
            local_dir="downloads/20260604/firebot",
        ),
    )
    @patch(
        "utils.cli_utils.build_local_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=2,
            local_dir="downloads/20260604/trendobot",
        ),
    )
    @patch("utils.cli_utils.run_with_timeout")
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot", "trendobot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_timed_out_download_uses_local_files_when_available(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        run_with_timeout,
        build_local_bot_artifacts,
        _download_bot_artifacts,
        build_report_data,
        write_report,
    ) -> None:
        call_count = 0

        def maybe_timeout(_timeout_seconds, callback):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise BotProcessingTimeout()
            return callback()

        run_with_timeout.side_effect = maybe_timeout

        result = main(["20260604"])

        self.assertEqual(result, 0)
        build_local_bot_artifacts.assert_called_once()
        self.assertEqual(build_report_data.call_count, 2)
        self.assertEqual(
            set(write_report.call_args.args[3]),
            {"firebot", "trendobot"},
        )

    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.resolve_artifact_prefix", return_value=None)
    @patch(
        "utils.cli_utils.candidate_artifact_prefixes",
        return_value=["missing-prefix/"],
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["trendobot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_all_missing_production_returns_error(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _candidate_artifact_prefixes,
        _resolve_artifact_prefix,
        write_report,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 1)
        write_report.assert_not_called()

    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="mock",
            downloaded_artifact_files=1,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="mock"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_mock_artifacts_write_mock_report_without_upstox(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _download_bot_artifacts,
        build_report_data,
        write_report,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 0)
        self.assertEqual(write_report.call_args.args[1].name, "20260604_mock_report.xlsx")
        self.assertIsNone(build_report_data.call_args.args[2])

    @patch.dict("utils.cli_utils.os.environ", {}, clear=True)
    @patch("utils.cli_utils.download_bot_artifacts")
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_production_artifacts_require_upstox_token(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        download_bot_artifacts,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 1)
        download_bot_artifacts.assert_not_called()

    @patch.dict(
        "utils.cli_utils.os.environ",
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=SimpleNamespace(
            warnings=[],
            artifact_kind="production",
            downloaded_artifact_files=1,
        ),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefix",
        return_value=SimpleNamespace(base_prefix="base-prefix", artifact_kind="production"),
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_production_artifacts_use_upstox_fetcher(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefix,
        _download_bot_artifacts,
        build_report_data,
        write_report,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 0)
        self.assertEqual(write_report.call_args.args[1].name, "20260604_report.xlsx")
        self.assertIsNotNone(build_report_data.call_args.args[2])
