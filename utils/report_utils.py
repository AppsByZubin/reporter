from __future__ import annotations

from datetime import date
from typing import Any

from common.models import BotArtifacts
from utils.log_utils import observation_from_log
from utils.record_utils import (
    extract_record_trade_id,
    extract_trade_rows,
    filter_event_records_by_creation_date,
    filter_records_by_date,
    load_records,
)


def build_report_data(
    artifacts: BotArtifacts,
    report_date: date | None = None,
) -> tuple[list[dict[str, Any]], str]:
    raw_order_log_records = load_records(artifacts.order_log_file)
    raw_order_event_records = load_records(artifacts.order_events_file)

    order_log_records = raw_order_log_records
    order_event_records = raw_order_event_records
    warnings = list(artifacts.warnings or [])

    if report_date is not None:
        order_log_records = filter_records_by_date(raw_order_log_records, report_date)
        order_log_trade_ids = {
            trade_id
            for record in order_log_records
            if (trade_id := extract_record_trade_id(record))
        }
        order_event_records = filter_event_records_by_creation_date(
            raw_order_event_records,
            report_date,
            order_log_trade_ids,
        )
        warnings.append(
            f"Filtered to {report_date.isoformat()}: "
            f"order_log records={len(order_log_records)}/{len(raw_order_log_records)}, "
            f"order_event records={len(order_event_records)}/{len(raw_order_event_records)}"
        )

    rows = extract_trade_rows(order_log_records, order_event_records)
    observation = observation_from_log(
        artifacts.bot,
        artifacts.log_file,
        len(order_log_records),
        len(order_event_records),
        warnings,
    )
    return rows, observation
