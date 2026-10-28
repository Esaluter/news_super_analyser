"""Core synchronization logic, independent from the Google client library."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .sheets import SheetsGateway, retry_after_seconds

SOURCE_HEADERS = (
    "record_key",
    "Дата сообщения",
    "Группа",
    "Отправитель",
    "Источник пересылки",
    "Текст новости",
    "Ссылки",
    "Хештеги",
    "ID сообщения",
    "ID группы",
    "Дата сбора",
)
TARGET_HEADERS = SOURCE_HEADERS + ("Статус обработки",)
PENDING_STATUS = "Ожидает анализа"
UTC_PLUS_FIVE = timezone(timedelta(hours=5))
GOOGLE_SHEETS_CELL_LIMIT = 50_000


@dataclass(frozen=True)
class SyncResult:
    rows_read: int = 0
    new_rows: int = 0
    duplicates: int = 0
    errors: int = 0


def format_source_date(value: Any, logger: logging.Logger, field: str, key: str) -> str:
    text = "" if value is None else str(value)
    if not text:
        return text
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise ValueError("timezone offset is missing")
        return parsed.astimezone(UTC_PLUS_FIVE).strftime("%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Не удалось разобрать %s для record_key=%s (%r): %s; сохранено исходное значение",
            field,
            key,
            text,
            exc,
        )
        return text


def _normalize_row(row: list[Any], width: int) -> list[str]:
    values = ["" if value is None else str(value) for value in row[:width]]
    return values + [""] * (width - len(values))


def _validate_headers(rows: list[list[Any]], expected: tuple[str, ...], sheet: str) -> None:
    if not rows:
        raise ValueError(f"Лист {sheet} пуст: отсутствует строка заголовков")
    actual = _normalize_row(rows[0], len(expected))
    if tuple(actual) != expected:
        raise ValueError(
            f"Неожиданные заголовки листа {sheet}: ожидались {expected}, получены {tuple(actual)}"
        )


def _target_keys(rows: list[list[Any]]) -> set[str]:
    return {
        str(row[0]).strip()
        for row in rows[1:]
        if row and str(row[0]).strip()
    }


class Synchronizer:
    def __init__(
        self,
        gateway: SheetsGateway,
        *,
        logger: logging.Logger | None = None,
        write_attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gateway = gateway
        self.logger = logger or logging.getLogger(__name__)
        self.write_attempts = write_attempts
        self.base_delay = base_delay
        self.sleep = sleep
        self._run_lock = threading.Lock()

    def run(self) -> SyncResult:
        if not self._run_lock.acquire(blocking=False):
            self.logger.warning("Синхронизация уже выполняется; параллельный запуск пропущен")
            return SyncResult()
        started = datetime.now(UTC_PLUS_FIVE)
        self.logger.info("Запуск синхронизации: %s", started.isoformat(timespec="seconds"))
        try:
            result = self._run_locked()
            return result
        finally:
            finished = datetime.now(UTC_PLUS_FIVE)
            self.logger.info("Завершение синхронизации: %s", finished.isoformat(timespec="seconds"))
            self._run_lock.release()

    def _run_locked(self) -> SyncResult:
        source_rows = self.gateway.read_source_rows()
        target_rows = self.gateway.read_target_rows()
        _validate_headers(source_rows, SOURCE_HEADERS, "ИЖС-КОРП")
        _validate_headers(target_rows, TARGET_HEADERS, "НОВОСТИ")

        data_rows = source_rows[1:] if source_rows else []
        known_keys = _target_keys(target_rows) if target_rows else set()
        seen_source: set[str] = set()
        new_rows = duplicates = errors = 0

        for source_number, raw_row in enumerate(data_rows, start=2):
            try:
                row = _normalize_row(raw_row, len(SOURCE_HEADERS))
                key = row[0].strip()
                if not key:
                    errors += 1
                    self.logger.error(
                        "Строка %d пропущена: отсутствует обязательный record_key",
                        source_number,
                    )
                    continue
                row[0] = key
                if key in seen_source:
                    duplicates += 1
                    self.logger.warning(
                        "Строка %d пропущена: повтор record_key=%s в источнике",
                        source_number,
                        key,
                    )
                    continue
                seen_source.add(key)
                if key in known_keys:
                    duplicates += 1
                    continue

                row[1] = format_source_date(row[1], self.logger, "Дата сообщения", key)
                row[10] = format_source_date(row[10], self.logger, "Дата сбора", key)
                self._validate_cell_lengths(row, key)
                if self._append_safely(row + [PENDING_STATUS], key):
                    known_keys.add(key)
                    new_rows += 1
                else:
                    errors += 1
            except Exception as exc:
                errors += 1
                self.logger.exception(
                    "Строка %d (record_key=%s) пропущена: %s",
                    source_number,
                    raw_row[0] if raw_row else "",
                    exc,
                )

        result = SyncResult(len(data_rows), new_rows, duplicates, errors)
        self.logger.info(
            "Итог: прочитано=%d, новых=%d, дублей=%d, ошибок=%d",
            result.rows_read,
            result.new_rows,
            result.duplicates,
            result.errors,
        )
        return result

    @staticmethod
    def _validate_cell_lengths(row: list[str], key: str) -> None:
        for column, value in zip(SOURCE_HEADERS, row):
            if len(value) > GOOGLE_SHEETS_CELL_LIMIT:
                raise ValueError(
                    f"значение {column!r} для record_key={key} содержит {len(value)} "
                    f"символов и превышает лимит Google Sheets {GOOGLE_SHEETS_CELL_LIMIT}; "
                    "значение не обрезано"
                )

    def _append_safely(self, row: list[str], key: str) -> bool:
        for attempt in range(1, self.write_attempts + 1):
            try:
                self.gateway.append_target_row(row)
                return True
            except Exception as exc:
                self.logger.warning(
                    "Ошибка записи record_key=%s (попытка %d/%d): %s; проверка назначения",
                    key,
                    attempt,
                    self.write_attempts,
                    exc,
                )
                try:
                    refreshed = self.gateway.read_target_rows()
                    _validate_headers(refreshed, TARGET_HEADERS, "НОВОСТИ")
                    if key in _target_keys(refreshed):
                        self.logger.info(
                            "record_key=%s обнаружен после ошибки записи; повтор не требуется",
                            key,
                        )
                        return True
                except Exception as read_exc:
                    self.logger.error(
                        "Не удалось проверить назначение после ошибки record_key=%s: %s; "
                        "повторная запись в этом цикле небезопасна",
                        key,
                        read_exc,
                    )
                    return False
                if attempt < self.write_attempts:
                    retry_after = retry_after_seconds(exc)
                    delay = (
                        retry_after
                        if retry_after is not None
                        else self.base_delay * (2 ** (attempt - 1))
                    )
                    self.sleep(delay)
        self.logger.error("Не удалось подтвердить запись record_key=%s", key)
        return False

