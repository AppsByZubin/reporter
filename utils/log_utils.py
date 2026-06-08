from __future__ import annotations

import re
from pathlib import Path


def observation_from_log(
    bot: str,
    log_file: Path | None,
    order_log_count: int,
    order_event_count: int,
    warnings: list[str],
) -> str:
    lines = [
        f"{bot}: order_log records={order_log_count}, order_event records={order_event_count}"
    ]
    lines.extend(warnings)

    if log_file is None or not log_file.exists():
        return "\n".join(lines)

    interesting: list[str] = []
    fallback: list[str] = []
    keywords = (
        "observation",
        "error",
        "exception",
        "warning",
        "warn",
        "failed",
        "failure",
        "reject",
        "entry",
        "exit",
        "trail",
        "stoploss",
        "target",
    )
    for raw_line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        fallback.append(line)
        if any(keyword in line.lower() for keyword in keywords):
            interesting.append(line)

    selected = interesting[-8:] if interesting else fallback[-8:]
    lines.extend(selected)
    return "\n".join(lines[:12])
