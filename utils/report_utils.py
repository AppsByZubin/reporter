from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal
from typing import Any, Callable

from common.models import BotArtifacts
from utils.log_utils import observation_from_log
from utils.record_utils import (
    collect_order_values,
    decimal_or_none,
    extract_record_datetime,
    extract_record_trade_id,
    extract_trade_rows,
    filter_event_records_by_creation_date,
    filter_records_by_date,
    fill_amount_if_possible,
    format_trade_order_ids,
    load_records,
    merge_missing,
    merge_order_id_fields,
    parse_trade_order_id_text,
    report_row_from_record,
)

ENTRY_ORDER_ID_PATHS = [
    "entry_order_ids",
    "entry_order_id",
    "buy_order_ids",
    "buy_order_id",
    "entry_broker_order_ids",
    "entry_broker_order_id",
    "response.entry_order_ids",
    "response.entry_order_id",
    "response.buy_order_ids",
    "response.buy_order_id",
    "entry_order_refs.order_id",
    "entry_order_refs.broker_order_id",
    "response.entry_order_refs.order_id",
    "response.entry_order_refs.broker_order_id",
    "entry_orders.order_id",
    "entry_orders.broker_order_id",
    "response.entry_orders.order_id",
    "response.entry_orders.broker_order_id",
    "order_ids.entry",
    "order_ids.buy",
    "response.order_ids.entry",
    "response.order_ids.buy",
]

SL_ORDER_ID_PATHS = [
    "sl_order_ids",
    "sl_order_id",
    "stop_loss_order_ids",
    "stop_loss_order_id",
    "stoploss_order_ids",
    "stoploss_order_id",
    "sl_broker_order_ids",
    "sl_broker_order_id",
    "response.sl_order_ids",
    "response.sl_order_id",
    "response.stop_loss_order_ids",
    "response.stop_loss_order_id",
    "response.stoploss_order_ids",
    "response.stoploss_order_id",
    "sl_order_refs.order_id",
    "sl_order_refs.broker_order_id",
    "response.sl_order_refs.order_id",
    "response.sl_order_refs.broker_order_id",
    "sl_orders.order_id",
    "sl_orders.broker_order_id",
    "response.sl_orders.order_id",
    "response.sl_orders.broker_order_id",
    "order_ids.sl",
    "order_ids.stop_loss",
    "order_ids.stoploss",
    "response.order_ids.sl",
    "response.order_ids.stop_loss",
    "response.order_ids.stoploss",
]


def build_report_data(
    artifacts: BotArtifacts,
    report_date: date | None = None,
    order_detail_fetcher: Callable[[str], dict[str, Any]] | None = None,
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

    order_log_records = sort_records_by_timestamp(order_log_records)

    if order_detail_fetcher is not None:
        rows = extract_trade_rows_from_order_log(order_log_records)
        rows = enrich_rows_with_order_details(
            rows,
            order_detail_fetcher,
            warnings,
        )
    else:
        rows = extract_trade_rows(order_log_records, order_event_records)
    observation = observation_from_log(
        artifacts.bot,
        artifacts.log_file,
        len(order_log_records),
        len(order_event_records),
        warnings,
    )
    return rows, observation


def sort_records_by_timestamp(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for _index, record in sorted(
            enumerate(records),
            key=lambda item: timestamp_sort_key(item[0], item[1]),
        )
    ]


def timestamp_sort_key(index: int, record: dict[str, Any]) -> tuple[int, float, int]:
    timestamp = extract_record_datetime(record)
    if timestamp is None:
        return (1, 0, index)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (0, timestamp.timestamp(), index)


def extract_trade_rows_from_order_log(
    order_log_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for record in order_log_records:
        row = report_row_from_record(record)
        if any(value not in (None, "") for value in row.values()):
            rows.append(row)

    return rows


def enrich_rows_with_order_details(
    rows: list[dict[str, Any]],
    order_detail_fetcher: Callable[[str], dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    enriched_rows: list[dict[str, Any]] = []

    for row in rows:
        order_ids_by_label = parse_trade_order_id_text(row.get("broker_order_id"))
        entry_order_ids = order_ids_by_label.get("Buy", [])
        sl_order_ids = order_ids_by_label.get("SL", [])
        all_order_ids = entry_order_ids + sl_order_ids

        if not all_order_ids:
            warnings.append(
                f"Trade {row.get('trade_id') or '<unknown>'}: "
                f"no order IDs found in order_log.csv for Upstox lookup."
            )
            enriched_rows.append(row)
            continue

        order_details: dict[str, dict[str, Any]] = {}
        for order_id in all_order_ids:
            if order_id not in cache:
                cache[order_id] = order_detail_fetcher(order_id)
            order_details[order_id] = cache[order_id]

        enriched_rows.append(
            row_from_upstox_order_details(
                row,
                entry_order_ids,
                sl_order_ids,
                order_details,
            )
        )

    if rows:
        warnings.append(
            f"Enriched {len(enriched_rows)} production row(s) with "
            f"{len(cache)} Upstox order detail lookup(s)."
        )
    return enriched_rows


def collect_event_order_ids_by_trade_id(
    order_event_records: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    order_ids_by_trade_id: dict[str, dict[str, list[str]]] = {}

    for record in order_event_records:
        trade_id = extract_record_trade_id(record)
        if not trade_id:
            continue

        trade_order_ids = order_ids_by_trade_id.setdefault(
            trade_id,
            {"Buy": [], "SL": []},
        )
        append_unique(
            trade_order_ids["Buy"],
            collect_order_values(record, ENTRY_ORDER_ID_PATHS),
        )
        append_unique(
            trade_order_ids["SL"],
            collect_order_values(record, SL_ORDER_ID_PATHS),
        )

    return order_ids_by_trade_id


def extract_trade_rows_from_order_events(
    order_event_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_trade_id: dict[str, dict[str, Any]] = {}
    rows_without_trade_id: list[dict[str, Any]] = []

    for record in order_event_records:
        row = report_row_from_record(record)
        if not any(value not in (None, "") for value in row.values()):
            continue

        trade_id = row.get("trade_id")
        if trade_id in (None, ""):
            rows_without_trade_id.append(row)
            continue

        key = str(trade_id)
        if key not in rows_by_trade_id:
            rows_by_trade_id[key] = row
            continue

        merge_missing(rows_by_trade_id[key], row)
        merge_order_id_fields(rows_by_trade_id[key], row)
        fill_amount_if_possible(rows_by_trade_id[key])

    return list(rows_by_trade_id.values()) + rows_without_trade_id


def append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def row_from_upstox_order_details(
    source_row: dict[str, Any],
    entry_order_ids: list[str],
    sl_order_ids: list[str],
    order_details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = dict(source_row)
    entry_details = [order_details[order_id] for order_id in entry_order_ids]
    sl_details = [order_details[order_id] for order_id in sl_order_ids]
    first_entry = first_detail(entry_details)
    first_sl = first_detail(sl_details)

    if first_entry:
        row["instrument"] = first_value(
            first_entry,
            ("trading_symbol", "tradingsymbol"),
            row.get("instrument"),
        )
        row["instrument_key"] = first_value(
            first_entry,
            ("instrument_token",),
            row.get("instrument_key"),
        )
        row["side"] = first_value(first_entry, ("transaction_type",), row.get("side"))
        row["timestamp"] = first_value(
            first_entry,
            ("order_timestamp", "exchange_timestamp"),
            row.get("timestamp"),
        )

    row["broker_order_id"] = format_trade_order_ids(entry_order_ids, sl_order_ids)
    row["exchange_order_id"] = format_trade_order_ids(
        exchange_order_ids(entry_details),
        exchange_order_ids(sl_details),
    ) or row.get("exchange_order_id")

    entry_price = average_filled_price(entry_details)
    if entry_price is not None:
        row["entry"] = entry_price

    exit_price = average_filled_price(sl_details)
    if exit_price is not None:
        row["exit"] = exit_price
        if first_sl:
            row["exit_timestamp"] = first_value(
                first_sl,
                ("exchange_timestamp", "order_timestamp"),
                row.get("exit_timestamp"),
            )

    qty = total_filled_quantity(entry_details)
    if qty is not None:
        row["qty"] = qty

    fill_amount_if_possible(row)
    return row


def first_detail(details: list[dict[str, Any]]) -> dict[str, Any] | None:
    return details[0] if details else None


def first_value(
    detail: dict[str, Any],
    keys: tuple[str, ...],
    fallback: Any = None,
) -> Any:
    for key in keys:
        value = detail.get(key)
        if value not in (None, ""):
            return value
    return fallback


def exchange_order_ids(details: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for detail in details:
        value = detail.get("exchange_order_id")
        if value not in (None, ""):
            values.append(str(value))
    return values


def average_filled_price(details: list[dict[str, Any]]) -> Decimal | None:
    total_quantity = Decimal("0")
    total_value = Decimal("0")
    fallback_prices: list[Decimal] = []

    for detail in details:
        price = decimal_or_none(detail.get("average_price"))
        if price is None or price == 0:
            price = decimal_or_none(detail.get("price"))
        if price is None:
            continue

        quantity = decimal_or_none(detail.get("filled_quantity"))
        if quantity is None or quantity == 0:
            fallback_prices.append(price)
            continue

        total_quantity += quantity
        total_value += price * quantity

    if total_quantity:
        return total_value / total_quantity
    if fallback_prices:
        return sum(fallback_prices, Decimal("0")) / Decimal(len(fallback_prices))
    return None


def total_filled_quantity(details: list[dict[str, Any]]) -> Decimal | None:
    total = Decimal("0")
    for detail in details:
        quantity = decimal_or_none(detail.get("filled_quantity"))
        if quantity is not None:
            total += quantity
    return total if total else None
