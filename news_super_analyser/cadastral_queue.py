"""Google Sheets queue synchronization and one-pass local cadastral processing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .cadastral_enrichment import (
    CadastralEnrichmentProcessor,
    CadastralLimits,
    CadastralSearcher,
)


STATUS_PENDING = "ОЖИДАЕТ"
STATUS_FOUND = "НАЙДЕНО"
STATUS_AMBIGUOUS = "НЕОДНОЗНАЧНО"
STATUS_NOT_FOUND = "НЕ НАЙДЕНО"
STATUS_ERROR = "ОШИБКА"


class CadastralQueueGateway(Protocol):
    def sync_cadastral_queue(self) -> dict[str, int]: ...

    def read_cadastral_tasks(self, limit: int) -> dict[str, Any]: ...

    def update_cadastral_task(self, **values: str) -> str: ...


@dataclass(frozen=True)
class QueueSyncStats:
    records_added: int = 0
    results_received: int = 0
    address_resets: int = 0
    formulas_prepared: int = 0
    objects_checked: int = 0
    errors: int = 0


@dataclass
class LocalQueueStats:
    total_records: int = 0
    checked: int = 0
    found: int = 0
    ambiguous: int = 0
    not_found: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0


class CadastralQueueSynchronizer:
    def __init__(
        self,
        gateway: CadastralQueueGateway,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.logger = logger or logging.getLogger(__name__)

    def run(self) -> QueueSyncStats:
        started = time.monotonic()
        values = self.gateway.sync_cadastral_queue()
        result = QueueSyncStats(**values)
        self.logger.info(
            "Кадастровая очередь синхронизирована; объектов=%d добавлено=%d "
            "получено=%d сброшено=%d формул_Q=%d время=%.2fс",
            result.objects_checked,
            result.records_added,
            result.results_received,
            result.address_resets,
            result.formulas_prepared,
            time.monotonic() - started,
        )
        return result


class _SingleTaskGateway:
    def __init__(self, task: dict[str, str]) -> None:
        self.task = task

    def read_object_enrichment_candidates(
        self, limit: int, *, row_number: int | None = None
    ) -> list[dict[str, Any]]:
        del limit, row_number
        analysis_id, separator, object_number = self.task["object_id"].rpartition("|")
        if not separator or not analysis_id or not object_number:
            raise ValueError("invalid composite object ID")
        return [
            {
                "row_number": 2,
                "analysis_id": analysis_id,
                "object_number": object_number,
                "name": "",
                "organization": "",
                "region": self.task["region"],
                "locality": self.task["locality"],
                "address": self.task["address"],
                # A pending/manual recheck must perform a fresh search. Previous
                # values are retained separately and restored only on an error.
                "cadastral_number": "",
                "cadastral_quarter": "",
                "all_cadastral": "",
                "gemini": "-",
            }
        ]

    def update_object_enrichment(self, **values: Any) -> dict[str, Any]:
        del values
        raise AssertionError("local queue planning must not write the objects sheet")


class LocalCadastralQueueProcessor:
    def __init__(
        self,
        gateway: CadastralQueueGateway,
        searcher: CadastralSearcher,
        *,
        limits: CadastralLimits,
        logger: logging.Logger | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.gateway = gateway
        self.searcher = searcher
        self.limits = limits
        self.logger = logger or logging.getLogger(__name__)
        self.sleep = sleep

    def run(self, limit: int) -> LocalQueueStats:
        started = time.monotonic()
        payload = self.gateway.read_cadastral_tasks(limit)
        tasks = payload["tasks"]
        stats = LocalQueueStats(total_records=int(payload["total_records"]))
        self.logger.info(
            "Локальный кадастровый проход начат; всего=%d заданий=%d",
            stats.total_records,
            len(tasks),
        )
        for index, task in enumerate(tasks):
            stats.checked += 1
            try:
                self._process_task(task, stats)
            except Exception as exc:
                stats.errors += 1
                self.logger.exception(
                    "Кадастровая запись id=%s завершилась ошибкой типа=%s",
                    task.get("object_id", ""),
                    type(exc).__name__,
                )
            if self.limits.request_delay_seconds and index + 1 < len(tasks):
                self.sleep(self.limits.request_delay_seconds)
        stats.duration_seconds = round(time.monotonic() - started, 2)
        self.logger.info(
            "Локальный кадастровый проход завершён; всего=%d проверено=%d "
            "найдено=%d неоднозначно=%d не_найдено=%d ошибок=%d "
            "пропущено=%d время=%.2fс",
            stats.total_records,
            stats.checked,
            stats.found,
            stats.ambiguous,
            stats.not_found,
            stats.errors,
            stats.skipped,
            stats.duration_seconds,
        )
        return stats

    def _process_task(self, task: dict[str, str], stats: LocalQueueStats) -> None:
        self.logger.info(
            "Кадастровая запись id=%s регион=%r населённый_пункт=%r адрес=%r "
            "исходный_статус=%s",
            task["object_id"],
            task["region"],
            task["locality"],
            task["address"],
            task["status"],
        )
        planner = CadastralEnrichmentProcessor(
            _SingleTaskGateway(task),
            self.searcher,
            logger=self.logger,
            attempts=self.limits.attempts,
            request_delay=self.limits.request_delay_seconds,
            row_timeout_seconds=self.limits.row_timeout_seconds,
            stage_timeout_seconds=self.limits.stage_timeout_seconds,
            sleep=self.sleep,
        )
        plan_stats = planner.run(1, apply=False)
        diagnostic = plan_stats.search_diagnostics[0]
        for attempt in diagnostic.attempts:
            self.logger.info(
                "Кадастровая запись id=%s уровень=%d запрос=%r запросов=%d "
                "кандидатов=%d принято=%d отклонено=%d причины=%s статус=%s",
                task["object_id"],
                attempt.level,
                attempt.query,
                attempt.requests,
                attempt.returned,
                attempt.accepted,
                attempt.rejected,
                attempt.rejection_reasons,
                attempt.status,
            )
        plan = plan_stats.planned_rows[0] if plan_stats.planned_rows else {}
        cadastral_number = str(plan.get("cadastral_number", ""))
        cadastral_quarter = str(plan.get("cadastral_quarter", ""))
        all_cadastral = str(plan.get("all_cadastral", ""))
        numbers = [value for value in all_cadastral.splitlines() if value.strip()]
        technical_error = diagnostic.outcome in {
            "nspd_unavailable",
            "response_error",
            "request_timeout",
            "row_timeout",
            "stage_timeout",
        }
        error = ""
        if technical_error:
            status = STATUS_ERROR
            error = diagnostic.outcome
            cadastral_number = task["cadastral_number"]
            cadastral_quarter = task["cadastral_quarter"]
            all_cadastral = task["all_cadastral"]
        elif len(numbers) > 1:
            status = STATUS_AMBIGUOUS
            cadastral_number = ""
            error = f"несколько подходящих кадастровых номеров: {len(numbers)}"
        elif cadastral_number or cadastral_quarter or len(numbers) == 1:
            status = STATUS_FOUND
        else:
            status = STATUS_NOT_FOUND
            cadastral_number = cadastral_quarter = all_cadastral = ""

        checked_at = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")
        write_status = self.gateway.update_cadastral_task(
            object_id=task["object_id"],
            region=task["region"],
            locality=task["locality"],
            address=task["address"],
            expected_status=task["status"],
            cadastral_number=cadastral_number,
            cadastral_quarter=cadastral_quarter,
            all_cadastral=all_cadastral,
            status=status,
            error=error,
            checked_at=checked_at,
        )
        if write_status in {"stale", "not_found"}:
            stats.skipped += 1
            self.logger.warning(
                "Кадастровая запись id=%s не записана: %s",
                task["object_id"],
                write_status,
            )
            return
        if status == STATUS_FOUND:
            stats.found += 1
        elif status == STATUS_AMBIGUOUS:
            stats.ambiguous += 1
        elif status == STATUS_NOT_FOUND:
            stats.not_found += 1
        else:
            stats.errors += 1
        self.logger.info(
            "Кадастровая запись id=%s номер=%r квартал=%r все=%r статус=%s "
            "ошибка=%r запись=%s",
            task["object_id"],
            cadastral_number,
            cadastral_quarter,
            all_cadastral,
            status,
            error,
            write_status,
        )
