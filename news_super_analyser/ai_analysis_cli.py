"""Manual entry point for the isolated TG-02B stage."""

from __future__ import annotations

import argparse
import logging

from .ai_analysis import AiAnalysisProcessor
from .config import Settings
from .gigachat_client import CHAT_URL, GigaChatClient, GigaChatSettings
from .sheets import AppsScriptGateway, MAX_AI_BATCH_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ручной анализ новостей через GigaChat")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только показать кандидата")
    mode.add_argument("--apply", action="store_true", help="выполнить анализ и запись")
    parser.add_argument("--limit", type=int, default=1, help="порция 1–20 (по умолчанию 1)")
    parser.add_argument("--record-key", help="обработать конкретный record_key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_AI_BATCH_SIZE:
        raise SystemExit(f"--limit должен быть от 1 до {MAX_AI_BATCH_SIZE}")
    settings = Settings.from_env()
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logging.getLogger("news_super_analyser.ai"),
    )
    if args.apply:
        gigachat_settings = GigaChatSettings.from_env()
        print(f"GigaChat: url={CHAT_URL}; model={gigachat_settings.model}")
        client = GigaChatClient(gigachat_settings)
    else:
        client = _DryClient()
    processor = AiAnalysisProcessor(
        gateway,
        client,
    )
    candidates = processor.select(
        limit=args.limit,
        record_key=args.record_key,
        claim=args.apply,
    )
    if args.dry_run:
        if not candidates:
            print("Кандидаты не найдены.")
        for candidate in candidates:
            print(
                f"Кандидат: {candidate['record_key']}; "
                f"Gemini: {len(candidate['gemini_text'])} символов; "
                f"GigaChat: {len(candidate['giga_text'])} символов; "
                f"источники: {candidate['source_models']}"
            )
        print(f"Итого кандидатов: {len(candidates)}. Запросы GigaChat и запись не выполнялись.")
        return 0

    results = [processor.process(candidate) for candidate in candidates]
    for item in results:
        suffix = f"; ошибка: {item.error}" if item.error else ""
        print(
            f"{item.record_key}: {item.status}; analysis_id={item.analysis_id}; "
            f"объектов={item.objects}; источник={item.text_source or 'нет'}{suffix}"
        )
    successes = sum(item.status == "success" for item in results)
    print(f"Обработано: {len(results)}; успешно: {successes}; ошибок: {len(results) - successes}")
    return 0 if successes == len(results) else 1


class _DryClient:
    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("dry-run must not call GigaChat")


if __name__ == "__main__":
    raise SystemExit(main())
