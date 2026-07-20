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


def artifact(bot: str, artifact_kind: str, files: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        bot=bot,
        warnings=[],
        artifact_kind=artifact_kind,
        downloaded_artifact_files=files,
        local_dir=f"downloads/20260604/{bot}",
    )


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
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data")
    @patch("utils.cli_utils.has_production_orders", return_value=True)
    @patch("utils.cli_utils.download_bot_artifacts")
    @patch("utils.cli_utils.resolve_artifact_prefixes")
    @patch(
        "utils.cli_utils.read_bot_list",
        return_value=["firebot", "trendobot", "haemabot", "titanbot", "fibobot"],
    )
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_mixed_modes_write_two_reports_and_omit_empty_bots(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        resolve_artifact_prefixes,
        download_bot_artifacts,
        has_production_orders,
        build_report_data,
        write_report,
    ) -> None:
        resolve_artifact_prefixes.side_effect = [
            {"production": SimpleNamespace(base_prefix="fire-prefix")},
            {"mock": SimpleNamespace(base_prefix="trend-prefix")},
            {"mock": SimpleNamespace(base_prefix="haema-prefix")},
            {
                "production": SimpleNamespace(base_prefix="titan-prefix"),
                "mock": SimpleNamespace(base_prefix="titan-prefix"),
            },
            {"mock": SimpleNamespace(base_prefix="fibo-prefix")},
        ]
        download_bot_artifacts.side_effect = (
            lambda _client, _bucket, _holder, bot, _date, _root, _prefix, kind:
            artifact(bot, kind)
        )

        rows_by_bot_and_kind = {
            ("firebot", "production"): [{"trade_id": "fire-production"}],
            ("titanbot", "production"): [{"trade_id": "titan-production"}],
            ("trendobot", "mock"): [],
            ("haemabot", "mock"): [{"trade_id": "haema-mock"}],
            ("titanbot", "mock"): [{"trade_id": "titan-mock"}],
            ("fibobot", "mock"): [{"trade_id": "fibo-mock"}],
        }
        build_report_data.side_effect = lambda artifacts, _date, _fetcher: (
            rows_by_bot_and_kind[(artifacts.bot, artifacts.artifact_kind)],
            "observation",
        )

        result = main(["20260604"])

        self.assertEqual(result, 0)
        self.assertEqual(has_production_orders.call_count, 2)
        self.assertEqual(write_report.call_count, 2)
        reports = {call.args[1].name: call for call in write_report.call_args_list}
        production = reports["20260604_production_trade_report.xlsx"]
        mock = reports["20260604_mock_trade_report.xlsx"]
        self.assertEqual(production.args[2], ["firebot", "titanbot"])
        self.assertEqual(set(production.args[3]), {"firebot", "titanbot"})
        self.assertEqual(mock.args[2], ["haemabot", "titanbot", "fibobot"])
        self.assertEqual(set(mock.args[3]), {"haemabot", "titanbot", "fibobot"})

        fetchers = {
            (call.args[0].bot, call.args[0].artifact_kind): call.args[2]
            for call in build_report_data.call_args_list
        }
        self.assertIsNotNone(fetchers[("firebot", "production")])
        self.assertIsNotNone(fetchers[("titanbot", "production")])
        self.assertIsNone(fetchers[("haemabot", "mock")])
        self.assertIsNone(fetchers[("titanbot", "mock")])

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_ID": "C123",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_message_via_slack")
    @patch("utils.cli_utils.send_file_via_slack")
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([], ""))
    @patch("utils.cli_utils.has_production_orders", return_value=False)
    @patch("utils.cli_utils.download_bot_artifacts")
    @patch(
        "utils.cli_utils.resolve_artifact_prefixes",
        return_value={
            "production": SimpleNamespace(base_prefix="base-prefix"),
            "mock": SimpleNamespace(base_prefix="base-prefix"),
        },
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["titanbot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_zero_orders_send_slack_messages_without_workbooks_or_upstox_token(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefixes,
        download_bot_artifacts,
        _has_production_orders,
        _build_report_data,
        write_report,
        send_file_via_slack,
        send_message_via_slack,
    ) -> None:
        download_bot_artifacts.side_effect = (
            lambda _client, _bucket, _holder, bot, _date, _root, _prefix, kind:
            artifact(bot, kind)
        )

        result = main(["20260604", "--slack"])

        self.assertEqual(result, 0)
        write_report.assert_not_called()
        send_file_via_slack.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in send_message_via_slack.call_args_list],
            ["No orders found for Production.", "No orders found for Mock."],
        )

    @patch.dict(
        "utils.cli_utils.os.environ",
        {
            "SLACK_BOT_TOKEN": "xoxb-test-token",
            "SLACK_CHANNEL_ID": "C123",
            "UPSTOX_API_ACCESS_TOKEN": "upstox-token",
        },
        clear=True,
    )
    @patch("utils.cli_utils.send_message_via_slack")
    @patch("utils.cli_utils.send_file_via_slack")
    @patch("utils.cli_utils.write_report")
    @patch(
        "utils.cli_utils.build_report_data",
        return_value=([{"trade_id": "fire"}], ""),
    )
    @patch("utils.cli_utils.has_production_orders", return_value=True)
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=artifact("firebot", "production"),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefixes",
        return_value={"production": SimpleNamespace(base_prefix="base-prefix")},
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_slack_uploads_available_mode_and_messages_empty_mode(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefixes,
        _download_bot_artifacts,
        _has_production_orders,
        _build_report_data,
        _write_report,
        send_file_via_slack,
        send_message_via_slack,
    ) -> None:
        result = main(["20260604", "--slack"])

        self.assertEqual(result, 0)
        self.assertEqual(
            send_file_via_slack.call_args.args[0].name,
            "20260604_production_trade_report.xlsx",
        )
        send_message_via_slack.assert_called_once()
        self.assertEqual(
            send_message_via_slack.call_args.args[0],
            "No orders found for Mock.",
        )

    @patch.dict("utils.cli_utils.os.environ", {}, clear=True)
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data")
    @patch("utils.cli_utils.has_production_orders", return_value=True)
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        return_value=artifact("firebot", "production"),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefixes",
        return_value={"production": SimpleNamespace(base_prefix="base-prefix")},
    )
    @patch("utils.cli_utils.read_bot_list", return_value=["firebot"])
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_production_orders_require_upstox_token(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefixes,
        download_bot_artifacts,
        _has_production_orders,
        build_report_data,
        write_report,
    ) -> None:
        result = main(["20260604"])

        self.assertEqual(result, 1)
        download_bot_artifacts.assert_called_once()
        build_report_data.assert_not_called()
        write_report.assert_not_called()

    @patch.dict(
        "utils.cli_utils.os.environ",
        {"UPSTOX_API_ACCESS_TOKEN": "upstox-token"},
        clear=True,
    )
    @patch("utils.cli_utils.write_report")
    @patch("utils.cli_utils.build_report_data", return_value=([{"trade_id": "fire"}], ""))
    @patch("utils.cli_utils.has_production_orders", return_value=True)
    @patch(
        "utils.cli_utils.download_bot_artifacts",
        side_effect=[
            artifact("firebot", "production"),
            artifact("titanbot", "production"),
        ],
    )
    @patch("utils.cli_utils.run_with_timeout")
    @patch(
        "utils.cli_utils.build_local_bot_artifacts",
        return_value=artifact("trendobot", "production", files=0),
    )
    @patch(
        "utils.cli_utils.resolve_artifact_prefixes",
        return_value={"production": SimpleNamespace(base_prefix="base-prefix")},
    )
    @patch(
        "utils.cli_utils.read_bot_list",
        return_value=["firebot", "trendobot", "titanbot"],
    )
    @patch("utils.cli_utils.build_s3_client", return_value=(object(), "bucket"))
    @patch("utils.cli_utils.create_logger", return_value=FakeLogger())
    def test_timed_out_bot_is_skipped_and_next_bot_runs(
        self,
        _logger,
        _build_s3_client,
        _read_bot_list,
        _resolve_artifact_prefixes,
        _build_local_bot_artifacts,
        run_with_timeout,
        download_bot_artifacts,
        _has_production_orders,
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
        self.assertEqual(write_report.call_args.args[2], ["firebot", "titanbot"])
