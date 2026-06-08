from __future__ import annotations

from pathlib import Path


def read_bot_list(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Bot list not found: {path}")
    bots: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            bots.append(clean)
    if not bots:
        raise ValueError(f"No bots found in {path}")
    return bots
