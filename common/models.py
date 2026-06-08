from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class ReportDate:
    value: date

    @property
    def output(self) -> str:
        return self.value.strftime("%Y%m%d")

    @property
    def space_folder(self) -> str:
        return self.value.strftime("%d%m%y")

    @property
    def legacy_space_folder(self) -> str:
        return self.output

    @property
    def log_prefix(self) -> str:
        return self.value.strftime("%d-%m-%y")


@dataclass
class BotArtifacts:
    bot: str
    base_prefix: str
    local_dir: Path
    log_file: Path | None = None
    order_events_file: Path | None = None
    order_log_file: Path | None = None
    downloaded_production_files: int = 0
    warnings: list[str] | None = None

    def add_warning(self, message: str) -> None:
        if self.warnings is None:
            self.warnings = []
        self.warnings.append(message)
