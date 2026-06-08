from __future__ import annotations

from datetime import date, datetime

from common.models import ReportDate


def parse_execution_date(raw: str | None) -> ReportDate:
    if not raw:
        return ReportDate(date.today())

    text = raw.strip()
    formats = ("%Y%m%d", "%d%m%y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y")
    for fmt in formats:
        try:
            return ReportDate(datetime.strptime(text, fmt).date())
        except ValueError:
            pass
    raise ValueError(
        f"Unsupported execution date {raw!r}. Use YYYYMMDD, for example 20260604."
    )
