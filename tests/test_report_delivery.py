"""Exercise corrected CloudPe paths through workbook creation and Slack delivery."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from botocore.exceptions import ClientError
from openpyxl import load_workbook

from common.models import ReportDate
from utils.cli_utils import main, parse_args
from utils.s3_utils import build_local_bot_artifacts, resolve_artifact_prefixes


class ArtifactS3:
    def __init__(self, objects):
        self.objects = objects
        self.downloads = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        return {"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)][:MaxKeys]}

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        yield self.list_objects_v2(**kwargs)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def download_file(self, bucket, key, target):
        self.downloads.append(key)
        Path(target).write_bytes(self.objects[key])


class ReportDeliveryTests(TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_prefix_defaults_and_overrides(self):
        self.assertEqual(parse_args([]).s3_prefix, "trades")
        with patch.dict("os.environ", {"CLOUDPE_S3_PREFIX": "trades-staging"}):
            self.assertEqual(parse_args([]).s3_prefix, "trades-staging")
            self.assertEqual(parse_args(["--s3-prefix", "trades"]).s3_prefix, "trades")

    def test_corrected_date_wins_and_swapped_date_is_not_used(self):
        for bot in ("solobot", "trendobot", "fibobot", "firebot", "haemabot", "titanbot", "meanbot"):
            with self.subTest(bot=bot):
                root = f"trades-staging/{bot}"
                s3 = ArtifactS3({
                    f"{root}/{folder}/{mode}/orders/order_log.csv": b""
                    for folder in ("20261001", "011026", "20260110")
                    for mode in ("mock", "production")
                })
                resolved = resolve_artifact_prefixes(s3, "index-bucket", "/trades-staging/", bot, ReportDate(date(2026, 10, 1)))
                self.assertEqual(set(resolved), {"mock", "production"})
                self.assertTrue(all(item.base_prefix == f"{root}/20261001/" for item in resolved.values()))
                wrong_date = ArtifactS3({f"{root}/20260110/mock/orders/order_log.csv": b""})
                self.assertEqual(resolve_artifact_prefixes(wrong_date, "index-bucket", "trades-staging", bot, ReportDate(date(2026, 10, 1))), {})

    @patch.dict("os.environ", {
        "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_ID": "C123",
        "UPSTOX_API_ACCESS_TOKEN": "upstox-test",
    }, clear=True)
    def test_corrected_paths_generate_dated_workbooks_for_slack(self):
        cases = (
            ("trades", [], {}),
            ("trades-staging", [], {"CLOUDPE_S3_PREFIX": "trades-staging"}),
            ("trades-staging", ["--s3-prefix", "/trades-staging/"], {}),
            ("index-bucket-holder/trades", ["--holder-prefix", "index-bucket-holder"], {}),
        )
        for prefix, args, env in cases:
            with self.subTest(prefix=prefix, args=args), TemporaryDirectory() as temp:
                root = Path(temp)
                bot_list = root / "bot.list"
                bot_list.write_text("titanbot\n")
                objects = {}
                for mode in ("mock", "production"):
                    base = f"{prefix}/titanbot/20261001/{mode}"
                    objects[f"{base}/orders/order_log.csv"] = (
                        "id,timestamp,entry_price,exit_price,qty\n"
                        f"{mode}-today,2026-10-01T10:00:00+05:30,100,110,10\n"
                        f"{mode}-yesterday,2026-09-30T10:00:00+05:30,100,110,10\n"
                    ).encode()
                    objects[f"{base}/orders/order_events.json"] = b'{"events": []}'
                    objects[f"{base}/logs/01-10-26_titanbot.log"] = b"No errors\n"
                s3 = ArtifactS3(objects)
                delivered = []

                def upload(path, settings):
                    self.assertEqual(settings.channel_id, "C123")
                    self.assertEqual(settings.title, "20261001 trade report")
                    workbook = load_workbook(path)
                    values = [cell.value for row in workbook.active for cell in row]
                    mode = "production" if "production" in path.name else "mock"
                    self.assertIn(f"{mode}-today", values)
                    self.assertNotIn(f"{mode}-yesterday", values)
                    workbook.close()
                    delivered.append(path.name)

                with patch.dict("os.environ", env), \
                     patch("utils.cli_utils.create_logger"), \
                     patch("utils.cli_utils.build_s3_client", return_value=(s3, "index-bucket")), \
                     patch("utils.cli_utils.UpstoxOrderClient.get_order_details") as upstox, \
                     patch("utils.cli_utils.send_file_via_slack", side_effect=upload), \
                     patch("utils.cli_utils.send_message_via_slack") as message:
                    result = main([
                        "20261001", "--slack", "--bot-list", str(bot_list),
                        "--download-dir", str(root / "downloads"),
                        "--output-dir", str(root / "output"), *args,
                    ])
                self.assertEqual(result, 0)
                self.assertEqual(delivered, [
                    "20261001_production_trade_report.xlsx", "20261001_mock_trade_report.xlsx",
                ])
                upstox.assert_not_called()
                message.assert_not_called()
                self.assertTrue(all("/20261001/" in key for key in s3.downloads))
                for mode in ("mock", "production"):
                    artifacts = build_local_bot_artifacts(
                        "titanbot", f"{prefix}/titanbot/20261001/", ReportDate(date(2026, 10, 1)), root / "downloads", mode,
                    )
                    self.assertEqual(artifacts.log_file, root / f"downloads/20261001/titanbot/{mode}/logs/01-10-26_titanbot.log")
