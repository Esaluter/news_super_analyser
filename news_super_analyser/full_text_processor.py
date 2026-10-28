"""Manual TG-02A batch processor, independent from TG-01 synchronization."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .telegram_full_text import (
    TelegramEmbedClient,
    TelegramPostResult,
    find_public_telegram_url,
)


UTC_PLUS_FIVE = timezone(timedelta(hours=5))
SHEET_STATUS = {
    "loaded": "Загружено",
    "unavailable": "Недоступно",
    "unsupported_url": "Неподдерживаемая ссылка",
    "error": "Ошибка",
}


class FullTextGateway(Protocol):
    def read_full_text_candidates(self, limit: int) -> list[dict[str, str]]: ...

    def update_full_text(
        self,
        *,
        record_key: str,
        full_text: str,
        load_status: str,
        loaded_at: str,
    ) -> str: ...

    def read_full_text_backfill_candidates(
        self, limit: int
    ) -> list[dict[str, str]]: ...

    def backfill_full_text(self, *, record_key: str) -> str: ...


@dataclass(frozen=True)
class ProcessingStats:
    candidates: int = 0
    fetched: int = 0
    fetch_failures: int = 0
    updated: int = 0
    already_processed: int = 0
    not_found: int = 0
    errors: int = 0


@dataclass(frozen=True)
class BackfillStats:
    candidates: int = 0
    updated: int = 0
    already_processed: int = 0
    no_source_text: int = 0
    not_found: int = 0
    errors: int = 0


class FullTextProcessor:
    def __init__(
        self,
        gateway: FullTextGateway,
        telegram: TelegramEmbedClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.telegram = telegram
        self.logger = logger or logging.getLogger(__name__)

    def run(self, limit: int) -> ProcessingStats:
        candidates = self.gateway.read_full_text_candidates(limit)
        fetched = fetch_failures = updated = already = not_found = errors = 0

        for candidate in candidates:
            key = candidate.get("record_key", "").strip()
            if not key:
                errors += 1
                self.logger.error("Кандидат пропущен: отсутствует record_key")
                continue

            url = find_public_telegram_url(candidate.get("links", ""))
            if url is None:
                result = TelegramPostResult(
                    url="",
                    status="unsupported_url",
                    reason="no_supported_public_telegram_url",
                )
            else:
                result = self.telegram.fetch(url)

            if result.successful:
                fetched += 1
            else:
                fetch_failures += 1
                self.logger.warning(
                    "Полный текст не получен для record_key=%s: status=%s reason=%s",
                    key,
                    result.status,
                    result.reason or "unknown",
                )

            source_text = candidate.get("source_text", "")
            full_text = result.text if result.successful else ""
            if not full_text and source_text.strip():
                full_text = source_text

            try:
                update_status = self.gateway.update_full_text(
                    record_key=key,
                    full_text=full_text,
                    load_status=SHEET_STATUS.get(result.status, "Ошибка"),
                    loaded_at=(
                        datetime.now(UTC_PLUS_FIVE).strftime("%d.%m.%Y %H:%M:%S")
                        if result.successful
                        else ""
                    ),
                )
            except Exception as exc:
                errors += 1
                self.logger.warning(
                    "Не удалось обновить M:O для record_key=%s: %s",
                    key,
                    type(exc).__name__,
                )
                continue

            if update_status == "updated":
                updated += 1
            elif update_status == "already_processed":
                already += 1
            elif update_status == "not_found":
                not_found += 1
            else:
                errors += 1

        stats = ProcessingStats(
            candidates=len(candidates),
            fetched=fetched,
            fetch_failures=fetch_failures,
            updated=updated,
            already_processed=already,
            not_found=not_found,
            errors=errors,
        )
        self.logger.info(
            "TG-02A: кандидатов=%d, текстов=%d, ошибок загрузки=%d, "
            "обновлено=%d, уже обработано=%d, не найдено=%d, ошибок записи=%d",
            stats.candidates,
            stats.fetched,
            stats.fetch_failures,
            stats.updated,
            stats.already_processed,
            stats.not_found,
            stats.errors,
        )
        return stats


class FullTextBackfillProcessor:
    """Fill only empty M cells from F without Telegram or AI requests."""

    def __init__(
        self,
        gateway: FullTextGateway,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.logger = logger or logging.getLogger(__name__)

    def run(self, limit: int) -> BackfillStats:
        candidates = self.gateway.read_full_text_backfill_candidates(limit)
        updated = already = no_source = not_found = errors = 0
        for candidate in candidates:
            key = candidate.get("record_key", "").strip()
            if not key:
                errors += 1
                continue
            try:
                status = self.gateway.backfill_full_text(record_key=key)
            except Exception as exc:
                errors += 1
                self.logger.warning(
                    "Не удалось заполнить M для record_key=%s: %s",
                    key,
                    type(exc).__name__,
                )
                continue
            if status == "updated":
                updated += 1
            elif status == "already_processed":
                already += 1
            elif status == "no_source_text":
                no_source += 1
            elif status == "not_found":
                not_found += 1
            else:
                errors += 1
        return BackfillStats(
            candidates=len(candidates),
            updated=updated,
            already_processed=already,
            no_source_text=no_source,
            not_found=not_found,
            errors=errors,
        )

