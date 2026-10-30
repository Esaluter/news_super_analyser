"""Fault-isolated orchestration of the existing TG-01 through TG-08 stages."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class StageOutcome:
    name: str
    successful: bool
    errors: int = 0
    detail: str = ""


@dataclass
class CycleReport:
    finished_at: datetime
    new_news: int = 0
    analyzed_news: int = 0
    processed_objects: int = 0
    cadastral_numbers_found: int = 0
    cadastral_records_added: int = 0
    cadastral_results_received: int = 0
    cadastre_mode: str = "sheets"
    errors: int = 0
    outcomes: list[StageOutcome] = field(default_factory=list)
    skipped_overlap: bool = False
    next_check_at: datetime | None = None

    @property
    def useful_work(self) -> bool:
        return any(
            (
                self.new_news,
                self.analyzed_news,
                self.processed_objects,
                self.cadastral_numbers_found,
                self.cadastral_records_added,
                self.cadastral_results_received,
            )
        )

    @property
    def status(self) -> str:
        if not self.errors:
            return "успешно"
        if self.useful_work or any(item.successful for item in self.outcomes):
            return "частично успешно"
        return "ошибка"

    @property
    def safe_error_summary(self) -> str:
        failed = [item.name for item in self.outcomes if item.errors]
        return ", ".join(failed[:4])


class CycleAlreadyRunning(RuntimeError):
    pass


class MonitoringPipeline:
    """Run every stage once; each stage receives all pending Sheets rows."""

    def __init__(
        self,
        *,
        sync: Callable[[], Any],
        full_text: Callable[[], Any],
        gemini: Callable[[], Any],
        giga_text: Callable[[], Any],
        analysis: Callable[[], Any],
        cadastral: Callable[[], Any],
        cadastre_mode: str = "sheets",
        logger: logging.Logger | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._stages = (
            ("сбор новостей", sync),
            ("полный текст", full_text),
            ("Gemini", gemini),
            ("GigaChat", giga_text),
            ("финальный анализ", analysis),
            ("кадастровое обогащение", cadastral),
        )
        self.logger = logger or logging.getLogger(__name__)
        self.cadastre_mode = cadastre_mode
        self._now = now
        self._lock = threading.Lock()

    def run_cycle(self) -> CycleReport:
        if not self._lock.acquire(blocking=False):
            raise CycleAlreadyRunning("цикл мониторинга уже выполняется")
        report = CycleReport(finished_at=self._now(), cadastre_mode=self.cadastre_mode)
        self.logger.info(
            "TG-09: полный цикл начат; кадастровый режим=%s", self.cadastre_mode
        )
        try:
            for name, stage in self._stages:
                self._run_stage(name, stage, report)
            report.finished_at = self._now()
            self.logger.info(
                "TG-09: цикл завершён; новых=%d анализов=%d объектов=%d "
                "кадастровых=%d ошибок=%d статус=%s",
                report.new_news,
                report.analyzed_news,
                report.processed_objects,
                report.cadastral_numbers_found,
                report.errors,
                report.status,
            )
            return report
        finally:
            self._lock.release()

    def _run_stage(
        self, name: str, stage: Callable[[], Any], report: CycleReport
    ) -> None:
        try:
            value = stage()
            errors = _result_errors(name, value)
            _merge_counts(name, value, report)
            report.errors += errors
            report.outcomes.append(StageOutcome(name, errors == 0, errors))
            self.logger.info("TG-09: этап %s завершён; ошибок=%d", name, errors)
        except Exception as exc:
            report.errors += 1
            report.outcomes.append(
                StageOutcome(name, False, 1, type(exc).__name__)
            )
            self.logger.warning(
                "TG-09: этап %s завершился ошибкой %s; переход к безопасному "
                "следующему этапу",
                name,
                type(exc).__name__,
            )


def _result_errors(name: str, value: Any) -> int:
    if isinstance(value, list):
        return sum(getattr(item, "status", "error") != "success" for item in value)
    fields = ("errors", "model_errors", "write_errors")
    total = sum(int(getattr(value, field, 0) or 0) for field in fields)
    if name == "полный текст":
        total += int(getattr(value, "fetch_failures", 0) or 0)
    return total


def _merge_counts(name: str, value: Any, report: CycleReport) -> None:
    if name == "сбор новостей":
        report.new_news += int(getattr(value, "new_rows", 0) or 0)
    elif name == "финальный анализ":
        items = value if isinstance(value, list) else []
        report.analyzed_news += sum(
            getattr(item, "status", "") == "success" for item in items
        )
        report.processed_objects += sum(
            int(getattr(item, "objects", 0) or 0)
            for item in items
            if getattr(item, "status", "") == "success"
        )
    elif name == "кадастровое обогащение":
        report.cadastral_numbers_found += int(
            getattr(value, "cadastral_numbers_found", 0) or 0
        )
        report.cadastral_records_added += int(
            getattr(value, "records_added", 0) or 0
        )
        report.cadastral_results_received += int(
            getattr(value, "results_received", 0) or 0
        )
