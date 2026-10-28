"""Manual TG-06 entry point for structured plain-text GigaChat analysis."""

from __future__ import annotations

import argparse
from dataclasses import replace

from .cli import configure_logging
from .config import ConfigurationError, Settings
from .giga_text import GigaTextProcessor
from .gigachat_client import GigaChatClient, GigaChatError, GigaChatSettings
from .sheets import AppsScriptGateway, MAX_GIGA_BATCH_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Структурированный plain-text анализ GigaChat в НОВОСТИ!Q"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать кандидатов")
    mode.add_argument("--apply", action="store_true", help="вызвать GigaChat и записать Q")
    parser.add_argument("--limit", type=int, default=1, help="порция 1–20")
    parser.add_argument("--record-key", help="обработать конкретную новость")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_GIGA_BATCH_SIZE:
        raise SystemExit(f"--limit должен быть от 1 до {MAX_GIGA_BATCH_SIZE}")
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

    if args.dry_run:
        processor = GigaTextProcessor(gateway, _DryClient(), logger=logger)
        candidates = processor.select(args.limit, record_key=args.record_key)
        for candidate in candidates:
            print(
                f"Кандидат: {candidate['record_key']}; "
                f"символов M: {len(candidate['full_text'])}; "
                f"результат: {candidate['target_cell']}"
            )
        print(
            f"Итого кандидатов: {len(candidates)}. GigaChat и запись не выполнялись."
        )
        return 0

    try:
        giga_settings = replace(
            GigaChatSettings.from_env(), structured_output=False
        )
    except GigaChatError as exc:
        print(f"Ошибка конфигурации GigaChat: {exc}")
        return 2
    processor = GigaTextProcessor(
        gateway,
        GigaChatClient(giga_settings),
        logger=logger,
    )
    stats = processor.run(args.limit, record_key=args.record_key)
    print(
        "TG-06: "
        f"кандидатов={stats.candidates}, "
        f"вызовов модели={stats.model_calls}, "
        f"записано={stats.updated}, "
        f"уже заполнено={stats.already_processed}, "
        f"нет полного текста={stats.no_full_text}, "
        f"не найдено={stats.not_found}, "
        f"ошибок модели={stats.model_errors}, "
        f"ошибок записи={stats.write_errors}"
    )
    return 1 if stats.model_errors or stats.write_errors else 0


class _DryClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("dry-run must not call GigaChat")


if __name__ == "__main__":
    raise SystemExit(main())
