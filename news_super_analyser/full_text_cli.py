"""Explicit manual entry point for Sprint 001 Telegram Full Text."""

from __future__ import annotations

import argparse

from .cli import configure_logging
from .config import ConfigurationError, Settings
from .full_text_processor import FullTextBackfillProcessor, FullTextProcessor
from .sheets import AppsScriptGateway, MAX_FULL_TEXT_BATCH_SIZE
from .telegram_full_text import TelegramEmbedClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ручная загрузка полного текста Telegram только в M:O"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help=f"число кандидатов, от 1 до {MAX_FULL_TEXT_BATCH_SIZE} (по умолчанию 1)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="минимальная пауза между запросами Telegram, секунд",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="обязательное подтверждение реального чтения и обновления M:O",
    )
    parser.add_argument(
        "--backfill-source-text",
        action="store_true",
        help="заполнить только пустую M из F без Telegram и ИИ; N:O не менять",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        print(
            "Реальная обработка не запущена. Добавьте --apply после проверки "
            "Apps Script, заголовков M:O и резервной копии таблицы."
        )
        return 2
    if not 1 <= args.limit <= MAX_FULL_TEXT_BATCH_SIZE:
        print(f"--limit должен быть от 1 до {MAX_FULL_TEXT_BATCH_SIZE}")
        return 2
    if args.request_delay < 0:
        print("--request-delay не может быть отрицательным")
        return 2

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}")
        return 2

    logger = configure_logging(str(settings.log_file))
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logger,
    )
    if args.backfill_source_text:
        processor = FullTextBackfillProcessor(gateway, logger=logger)
        try:
            stats = processor.run(args.limit)
        except Exception as exc:
            logger.warning("TG-04 backfill завершён до обработки: %s", type(exc).__name__)
            return 1
        print(
            "TG-04 backfill: "
            f"кандидатов={stats.candidates}, "
            f"обновлено={stats.updated}, "
            f"уже заполнено={stats.already_processed}, "
            f"нет исходного текста={stats.no_source_text}, "
            f"не найдено={stats.not_found}, "
            f"ошибок={stats.errors}"
        )
        return 1 if stats.errors else 0

    telegram = TelegramEmbedClient(
        timeout_seconds=settings.http_timeout_seconds,
        min_interval_seconds=args.request_delay,
    )
    processor = FullTextProcessor(gateway, telegram, logger=logger)
    try:
        stats = processor.run(args.limit)
    except Exception as exc:
        logger.warning("TG-02A завершён до обработки кандидатов: %s", type(exc).__name__)
        return 1

    print(
        "TG-02A: "
        f"кандидатов={stats.candidates}, "
        f"получено текстов={stats.fetched}, "
        f"ошибок загрузки={stats.fetch_failures}, "
        f"обновлено={stats.updated}, "
        f"уже обработано={stats.already_processed}, "
        f"не найдено={stats.not_found}, "
        f"ошибок записи={stats.errors}"
    )
    return 1 if stats.fetch_failures or stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

