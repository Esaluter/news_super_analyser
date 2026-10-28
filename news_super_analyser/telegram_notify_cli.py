"""Safe standalone Telegram notification check; network requires ``--send``."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from .config import ConfigurationError, load_project_environment
from .pipeline import CycleReport
from .telegram_notify import (
    TelegramNotificationError,
    TelegramNotifier,
    format_cycle_message,
)


@dataclass(frozen=True)
class TelegramCheckSettings:
    token: str
    chat_id: str
    sheet_url: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "TelegramCheckSettings":
        load_project_environment()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        sheet_url = os.getenv("GOOGLE_SHEET_URL", "").strip()
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", token),
                ("TELEGRAM_CHAT_ID", chat_id),
                ("GOOGLE_SHEET_URL", sheet_url),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Не заданы параметры Telegram-проверки: " + ", ".join(missing)
            )
        parsed = urlparse(sheet_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("GOOGLE_SHEET_URL должен быть HTTPS URL")
        try:
            timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS должен быть числом") from exc
        if timeout <= 0:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS должен быть больше нуля")
        return cls(token, chat_id, sheet_url, timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверка Telegram без запуска мониторинга"
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="явно отправить одно тестовое сообщение; без флага сеть не используется",
    )
    return parser


def _test_report() -> CycleReport:
    return CycleReport(finished_at=datetime.now())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = TelegramCheckSettings.from_env()
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    message = format_cycle_message(_test_report(), settings.sheet_url)
    chat_kind = "числовой chat_id" if settings.chat_id.lstrip("-").isdigit() else "публичное имя чата"
    print(
        "Конфигурация Telegram корректна: "
        f"{chat_kind}; HTML-сообщение={len(message)} символов; HTTPS-ссылка готова."
    )
    if not args.send:
        print("Сетевой запрос не выполнялся. Для одной реальной отправки добавьте --send.")
        return 0

    notifier = TelegramNotifier(
        token=settings.token,
        chat_id=settings.chat_id,
        sheet_url=settings.sheet_url,
        timeout_seconds=settings.timeout_seconds,
    )
    try:
        notifier.send(_test_report())
    except TelegramNotificationError as exc:
        print(f"Telegram отклонил тестовое сообщение: {exc}", file=sys.stderr)
        return 1
    print("Одно тестовое Telegram-сообщение отправлено.")
    return 0
