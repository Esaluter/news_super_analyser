"""Manual TG-05 entry point for preparing native Google Sheets AI formulas."""

from __future__ import annotations

import argparse
import logging

from .config import ConfigurationError, Settings
from .gemini_formula import GeminiFormulaProcessor
from .sheets import AppsScriptGateway, MAX_GEMINI_BATCH_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Подготовка формул =AI() в пустых ячейках НОВОСТИ!P"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать кандидатов")
    mode.add_argument("--apply", action="store_true", help="записать формулы в пустые P")
    parser.add_argument("--limit", type=int, default=1, help="порция 1–20")
    parser.add_argument("--record-key", help="подготовить конкретную новость")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_GEMINI_BATCH_SIZE:
        raise SystemExit(f"--limit должен быть от 1 до {MAX_GEMINI_BATCH_SIZE}")
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}")
        return 2
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logging.getLogger("news_super_analyser.gemini_formula"),
    )
    processor = GeminiFormulaProcessor(gateway)
    if args.dry_run:
        candidates = processor.select(args.limit, record_key=args.record_key)
        for candidate in candidates:
            print(
                f"Кандидат: {candidate['record_key']}; "
                f"формула {candidate['target_cell']} → {candidate['source_cell']}"
            )
        print(
            f"Итого кандидатов: {len(candidates)}. Формулы и вызовы Gemini не выполнялись."
        )
        return 0

    stats = processor.run(args.limit, record_key=args.record_key)
    print(
        "TG-05: "
        f"кандидатов={stats.candidates}, "
        f"подготовлено={stats.updated}, "
        f"уже заполнено={stats.already_processed}, "
        f"нет полного текста={stats.no_full_text}, "
        f"не найдено={stats.not_found}, "
        f"ошибок={stats.errors}"
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
