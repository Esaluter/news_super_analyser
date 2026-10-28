"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .config import ConfigurationError, Settings
from .sheets import AppsScriptGateway
from .sync import Synchronizer
from .safe_logging import configure_safe_logging


def configure_logging(log_file: str) -> logging.Logger:
    return configure_safe_logging(log_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Синхронизация новых публикаций из ИЖС-КОРП в НОВОСТИ"
    )
    parser.add_argument(
        "command", choices=("once", "schedule"), help="один запуск или постоянный цикл"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    logger = configure_logging(str(settings.log_file))
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logger,
    )
    synchronizer = Synchronizer(gateway, logger=logger)

    if args.command == "once":
        try:
            result = synchronizer.run()
        except Exception as exc:
            logger.exception("Синхронизация завершилась ошибкой: %s", exc)
            return 1
        return 1 if result.errors else 0

    logger.info(
        "Планировщик запущен, интервал %.2f мин.", settings.sync_interval_minutes
    )
    try:
        while True:
            try:
                synchronizer.run()
            except Exception as exc:
                logger.exception("Цикл синхронизации завершился ошибкой: %s", exc)
            time.sleep(settings.sync_interval_minutes * 60)
    except KeyboardInterrupt:
        logger.info("Планировщик остановлен пользователем")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

