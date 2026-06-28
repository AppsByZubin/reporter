from __future__ import annotations

import csv
import json
import re
from collections import OrderedDict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from common.constants import FIELD_ALIASES, REPORT_COLUMNS


DATE_FIELD_ALIASES = (
    "timestamp",
    "ts",
    "created_at",
    "createdat",
    "created_time",
    "createdtime",
    "entry_time",
    "entrytime",
    "entry_timestamp",
    "entrytimestamp",
    "trade_time",
    "tradetime",
    "time",
    "date",
    "exit_time",
    "exittime",
    "updated_at",
    "updatedat",
)


def load_records(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return coerce_records(data)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            clean = line.strip().rstrip(",")
            if not clean:
                continue
            try:
                records.extend(coerce_records(json.loads(clean)))
            except json.JSONDecodeError:
                continue
        return records


def coerce_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("orders", "events", "data", "records", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    return []


def flatten_record(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        clean_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_record(value, clean_key))
        else:
            flat[clean_key] = value
    return flat


def get_value(flat: dict[str, Any], column: str) -> Any:
    normalized = {normalize_key(key): value for key, value in flat.items()}
    for alias in FIELD_ALIASES.get(column, [column]):
        alias_key = normalize_key(alias)
        if alias_key in normalized:
            value = simplify_value(normalized[alias_key])
            if value not in (None, ""):
                return value
    return None


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", value.lower())


def simplify_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, bool, Decimal)):
        return value
    if isinstance(value, dict):
        for key in (
            "order_id",
            "broker_order_id",
            "exchange_order_id",
            "trade_id",
            "id",
        ):
            if value.get(key) not in (None, ""):
                return value[key]
        return json.dumps(value, sort_keys=True)
    if isinstance(value, (list, tuple)):
        parts = [simplify_value(item) for item in value]
        clean_parts = [str(part) for part in parts if part not in (None, "")]
        return ", ".join(clean_parts) if clean_parts else None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            return simplify_value(parsed)
        return text
    return value


def report_row_from_record(record: dict[str, Any]) -> dict[str, Any]:
    flat = flatten_record(record)
    row = {column: get_value(flat, column) for column in REPORT_COLUMNS}
    fill_order_ids_if_possible(row, record)
    fill_amount_if_possible(row)
    return row


def fill_order_ids_if_possible(row: dict[str, Any], record: dict[str, Any]) -> None:
    broker_order_id = format_trade_order_ids(
        collect_order_values(
            record,
            [
                "entry_order_ids",
                "response.entry_order_ids",
                "entry_order_refs.order_id",
                "entry_orders.order_id",
                "response.entry_orders.order_id",
            ],
        ),
        collect_order_values(
            record,
            [
                "sl_order_ids",
                "response.sl_order_ids",
                "sl_order_refs.order_id",
                "sl_orders.order_id",
                "response.sl_orders.order_id",
            ],
        ),
    )
    if broker_order_id:
        row["broker_order_id"] = broker_order_id

    exchange_order_id = format_trade_order_ids(
        collect_order_values(
            record,
            [
                "entry_exchange_order_ids",
                "entry_order_refs.exchange_order_id",
                "entry_orders.exchange_order_id",
                "response.entry_orders.exchange_order_id",
            ],
        ),
        collect_order_values(
            record,
            [
                "sl_exchange_order_ids",
                "sl_order_refs.exchange_order_id",
                "sl_orders.exchange_order_id",
                "response.sl_orders.exchange_order_id",
            ],
        ),
    )
    if exchange_order_id:
        row["exchange_order_id"] = exchange_order_id


def collect_order_values(record: dict[str, Any], paths: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for value in values_at_path(record, path.split(".")):
            text = str(value).strip()
            if text and text not in seen:
                values.append(text)
                seen.add(text)
    return values


def values_at_path(value: Any, parts: list[str]) -> list[Any]:
    value = parse_embedded_json(value)
    if value in (None, ""):
        return []
    if not parts:
        return flatten_order_values(value)

    if isinstance(value, dict):
        return values_at_path(value.get(parts[0]), parts[1:])
    if isinstance(value, (list, tuple)):
        values: list[Any] = []
        for item in value:
            values.extend(values_at_path(item, parts))
        return values
    return []


def flatten_order_values(value: Any) -> list[Any]:
    value = parse_embedded_json(value)
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        values: list[Any] = []
        for item in value:
            values.extend(flatten_order_values(item))
        return values
    return [value]


def parse_embedded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith(("[", "{")):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def format_trade_order_ids(entry_order_ids: list[str], sl_order_ids: list[str]) -> str | None:
    parts: list[str] = []
    if entry_order_ids:
        parts.append(f"Buy: {', '.join(entry_order_ids)}")
    if sl_order_ids:
        parts.append(f"SL: {', '.join(sl_order_ids)}")
    return "\n".join(parts) if parts else None


def merge_order_id_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("broker_order_id", "exchange_order_id"):
        source_value = source.get(key)
        if source_value in (None, ""):
            continue
        target_value = target.get(key)
        target[key] = merge_trade_order_id_text(target_value, source_value)


def merge_trade_order_id_text(target_value: Any, source_value: Any) -> str:
    merged = parse_trade_order_id_text(target_value)
    source = parse_trade_order_id_text(source_value)

    for label, values in source.items():
        merged.setdefault(label, [])
        for value in values:
            if value not in merged[label]:
                merged[label].append(value)

    parts: list[str] = []
    for label in ("Buy", "SL"):
        values = merged.get(label, [])
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    return "\n".join(parts)


def parse_trade_order_id_text(value: Any) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    if value in (None, ""):
        return parsed

    fallback_label = "Buy"
    for line in str(value).splitlines():
        text = line.strip()
        if not text:
            continue
        label = fallback_label
        ids_text = text
        if ":" in text:
            raw_label, ids_text = text.split(":", 1)
            normalized_label = raw_label.strip().lower()
            if normalized_label in ("buy", "entry"):
                label = "Buy"
            elif normalized_label in ("sl", "stoploss", "stop loss"):
                label = "SL"
        values = [part.strip() for part in ids_text.split(",") if part.strip()]
        if values:
            parsed.setdefault(label, [])
            for order_id in values:
                if order_id not in parsed[label]:
                    parsed[label].append(order_id)
    return parsed


def merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if target.get(key) in (None, "") and value not in (None, ""):
            target[key] = value


def filter_records_by_date(
    records: list[dict[str, Any]],
    target_date: date,
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if extract_record_date(record) == target_date
    ]


def filter_event_records_by_creation_date(
    records: list[dict[str, Any]],
    target_date: date,
    trade_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    target_trade_ids = set(trade_ids or set())
    has_event_types = False

    for record in records:
        event_type = extract_event_type(record)
        if event_type:
            has_event_types = True
        if is_trade_creation_event(event_type) and extract_record_date(record) == target_date:
            trade_id = extract_record_trade_id(record)
            if trade_id:
                target_trade_ids.add(trade_id)

    if target_trade_ids:
        return [
            record
            for record in records
            if extract_record_trade_id(record) in target_trade_ids
        ]
    if has_event_types:
        return []
    return filter_records_by_date(records, target_date)


def extract_record_date(record: dict[str, Any]) -> date | None:
    flat = flatten_record(record)
    normalized = {normalize_key(key): value for key, value in flat.items()}
    for alias in DATE_FIELD_ALIASES:
        value = normalized.get(normalize_key(alias))
        parsed = parse_date_value(value)
        if parsed is not None:
            return parsed
    return None


def extract_record_datetime(record: dict[str, Any]) -> datetime | None:
    flat = flatten_record(record)
    normalized = {normalize_key(key): value for key, value in flat.items()}
    for alias in DATE_FIELD_ALIASES:
        value = normalized.get(normalize_key(alias))
        parsed = parse_datetime_value(value)
        if parsed is not None:
            return parsed
    return None


def parse_datetime_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)

    text = str(value).strip()
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    match = re.search(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?",
        text,
    )
    if match:
        try:
            return datetime.fromisoformat(match.group(0))
        except ValueError:
            pass

    for fmt in (
        "%Y%m%d",
        "%d%m%y",
        "%Y-%m-%d",
        "%d-%m-%y",
        "%d/%m/%y",
        "%d/%m/%Y",
    ):
        try:
            parsed_date = datetime.strptime(text, fmt).date()
            return datetime.combine(parsed_date, time.min)
        except ValueError:
            pass
    return None


def parse_date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text).date()
    except ValueError:
        pass

    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        try:
            return date.fromisoformat(match.group(0))
        except ValueError:
            pass

    for fmt in ("%Y%m%d", "%d%m%y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def extract_record_trade_id(record: dict[str, Any]) -> str | None:
    value = get_value(flatten_record(record), "trade_id")
    if value in (None, ""):
        return None
    return str(value)


def extract_event_type(record: dict[str, Any]) -> str | None:
    normalized = {normalize_key(key): value for key, value in flatten_record(record).items()}
    for alias in ("event_type", "eventtype", "type", "event"):
        value = normalized.get(normalize_key(alias))
        if value not in (None, ""):
            return str(value)
    return None


def is_trade_creation_event(event_type: str | None) -> bool:
    if not event_type:
        return False
    normalized = normalize_key(event_type)
    return "create" in normalized and "trade" in normalized


def extract_trade_rows(
    order_log_records: list[dict[str, Any]],
    order_event_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_key: OrderedDict[tuple[Any, Any, int], dict[str, Any]] = OrderedDict()
    all_records = order_log_records or order_event_records

    for index, record in enumerate(all_records):
        row = report_row_from_record(record)

        if not any(row.get(column) not in (None, "") for column in REPORT_COLUMNS):
            continue

        key = (
            row.get("trade_id") or "",
            row.get("broker_order_id") or "",
            index if not (row.get("trade_id") or row.get("broker_order_id")) else -1,
        )
        if key not in rows_by_key:
            rows_by_key[key] = row
        else:
            merge_missing(rows_by_key[key], row)
            merge_order_id_fields(rows_by_key[key], row)
            fill_amount_if_possible(rows_by_key[key])

    event_rows_by_trade_id: dict[Any, dict[str, Any]] = {}
    for record in order_event_records:
        event_row = report_row_from_record(record)
        trade_id = event_row.get("trade_id")
        if trade_id in (None, ""):
            continue
        if trade_id not in event_rows_by_trade_id:
            event_rows_by_trade_id[trade_id] = event_row
        else:
            merge_missing(event_rows_by_trade_id[trade_id], event_row)
            merge_order_id_fields(event_rows_by_trade_id[trade_id], event_row)
            fill_amount_if_possible(event_rows_by_trade_id[trade_id])

    for key, row in rows_by_key.items():
        trade_id = key[0] or ""
        if trade_id in event_rows_by_trade_id:
            merge_missing(row, event_rows_by_trade_id[trade_id])
            merge_order_id_fields(row, event_rows_by_trade_id[trade_id])
            fill_amount_if_possible(row)

    return list(rows_by_key.values())


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def fill_amount_if_possible(row: dict[str, Any]) -> None:
    qty = decimal_or_none(row.get("qty"))
    entry = decimal_or_none(row.get("entry"))
    exit_price = decimal_or_none(row.get("exit"))
    if qty is not None and entry is not None and exit_price is not None:
        row["amount"] = (exit_price - entry) * qty


def clean_cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    numeric = decimal_or_none(text)
    if numeric is not None and re.fullmatch(r"-?[\d,]+(\.\d+)?", text):
        return float(numeric)
    return text
