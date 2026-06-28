import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from common.models import BotArtifacts
from utils.report_utils import build_report_data


class ReportUtilsTests(TestCase):
    def test_build_report_data_enriches_order_log_rows_in_timestamp_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            order_log = Path(temp_dir) / "order_log.csv"
            order_log.write_text(
                "\n".join(
                    [
                        "id,symbol,instrument_token,side,qty,entry_order_ids,sl_order_ids,timestamp",
                        (
                            'trade-2,BANKNIFTY,NSE_FO|2,BUY,5,"[""entry-2""]",'
                            '"[""sl-2""]",2026-06-25T10:05:00+05:30'
                        ),
                        (
                            'trade-1,NIFTY,NSE_FO|1,BUY,10,"[""entry-1""]",'
                            '"[""sl-1""]",2026-06-25T10:00:00+05:30'
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            order_events = Path(temp_dir) / "order_events.json"
            order_events.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "event_type": "CREATE_TRADE",
                                "trade_id": "trade-1",
                                "ts": "2026-06-25T10:00:00+05:30",
                                "symbol": "NIFTY",
                                "instrument_token": "NSE_FO|1",
                                "side": "BUY",
                                "qty": 10,
                                "entry_order_ids": ["event-entry-1"],
                            },
                            {
                                "event_type": "STOP_LOSS_CREATED",
                                "trade_id": "trade-1",
                                "ts": "2026-06-25T10:00:04+05:30",
                                "sl_order_ids": ["event-sl-1"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            artifacts = BotArtifacts(
                bot="firebot",
                base_prefix="base/",
                local_dir=Path(temp_dir),
                artifact_kind="production",
                order_log_file=order_log,
                order_events_file=order_events,
            )
            requested_order_ids = []

            def get_order_details(order_id: str) -> dict:
                requested_order_ids.append(order_id)
                return {
                    "entry-1": {
                        "order_id": "entry-1",
                        "exchange_order_id": "exchange-entry",
                        "trading_symbol": "NIFTY 24100 CE",
                        "instrument_token": "NSE_FO|79732",
                        "transaction_type": "BUY",
                        "average_price": 100,
                        "filled_quantity": 10,
                        "order_timestamp": "2026-06-25 10:00:02",
                    },
                    "sl-1": {
                        "order_id": "sl-1",
                        "exchange_order_id": "exchange-sl",
                        "average_price": 110,
                        "filled_quantity": 10,
                    },
                    "entry-2": {
                        "order_id": "entry-2",
                        "exchange_order_id": "exchange-entry-2",
                        "trading_symbol": "BANKNIFTY 52000 CE",
                        "instrument_token": "NSE_FO|79733",
                        "transaction_type": "BUY",
                        "average_price": 200,
                        "filled_quantity": 5,
                        "order_timestamp": "2026-06-25 10:05:02",
                    },
                    "sl-2": {
                        "order_id": "sl-2",
                        "exchange_order_id": "exchange-sl-2",
                        "average_price": 190,
                        "filled_quantity": 5,
                    },
                }[order_id]

            rows, observation = build_report_data(
                artifacts,
                date(2026, 6, 25),
                get_order_details,
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["trade_id"] for row in rows], ["trade-1", "trade-2"])
        self.assertEqual(requested_order_ids, ["entry-1", "sl-1", "entry-2", "sl-2"])
        row = rows[0]
        self.assertEqual(row["trade_id"], "trade-1")
        self.assertEqual(row["broker_order_id"], "Buy: entry-1\nSL: sl-1")
        self.assertEqual(row["exchange_order_id"], "Buy: exchange-entry\nSL: exchange-sl")
        self.assertEqual(row["instrument"], "NIFTY 24100 CE")
        self.assertEqual(row["instrument_key"], "NSE_FO|79732")
        self.assertEqual(row["entry"], 100)
        self.assertEqual(row["exit"], 110)
        self.assertEqual(row["qty"], 10)
        self.assertEqual(row["amount"], 100)
        self.assertIn("Enriched 2 production row", observation)
