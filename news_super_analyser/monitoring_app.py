"""Composition root for the autonomous TG-09 service."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import IO

from .ai_analysis import AiAnalysisProcessor
from .cadastral_enrichment import (
    MAX_OBJECT_BATCH_SIZE,
    CadastralLimits,
    CadastralEnrichmentProcessor,
    PynspdCadastralSearcher,
)
from .config import ConfigurationError, MonitoringSettings
from .full_text_processor import FullTextProcessor
from .gemini_formula import GeminiFormulaProcessor
from .giga_text import GigaTextProcessor
from .gigachat_client import GigaChatClient, GigaChatError, GigaChatSettings
from .monitoring_service import MonitoringService
from .pipeline import MonitoringPipeline
from .safe_logging import configure_safe_logging
from .sheets import (
    MAX_AI_BATCH_SIZE,
    MAX_FULL_TEXT_BATCH_SIZE,
    MAX_GEMINI_BATCH_SIZE,
    MAX_GIGA_BATCH_SIZE,
    AppsScriptGateway,
)
from .sync import Synchronizer
from .telegram_full_text import TelegramEmbedClient
from .telegram_notify import TelegramNotifier


class SingleInstanceLock:
    """Best-effort OS lock; Sheets/Apps Script remains the final idempotency layer."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("r+b") if self.path.exists() else self.path.open("w+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError("another monitoring instance is already running")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def build_service(
    settings: MonitoringSettings,
    *,
    logger: logging.Logger,
    stop_event: threading.Event,
    cadastral_limits: CadastralLimits,
) -> MonitoringService:
    gateway = AppsScriptGateway(
        settings.base.apps_script_url,
        settings.base.apps_script_secret,
        timeout_seconds=settings.base.http_timeout_seconds,
        logger=logger,
    )
    giga_settings = GigaChatSettings.from_env()
    pipeline = MonitoringPipeline(
        sync=Synchronizer(gateway, logger=logger).run,
        full_text=lambda: FullTextProcessor(
            gateway,
            TelegramEmbedClient(timeout_seconds=settings.base.http_timeout_seconds),
            logger=logger,
        ).run(MAX_FULL_TEXT_BATCH_SIZE),
        gemini=lambda: GeminiFormulaProcessor(gateway, logger=logger).run(
            MAX_GEMINI_BATCH_SIZE
        ),
        giga_text=lambda: GigaTextProcessor(
            gateway,
            GigaChatClient(replace(giga_settings, structured_output=False)),
            logger=logger,
        ).run(MAX_GIGA_BATCH_SIZE),
        analysis=lambda: AiAnalysisProcessor(
            gateway, GigaChatClient(giga_settings), logger=logger
        ).run(limit=MAX_AI_BATCH_SIZE),
        cadastral=lambda: CadastralEnrichmentProcessor(
            gateway,
            PynspdCadastralSearcher(
                timeout=cadastral_limits.http_timeout_seconds
            ),
            logger=logger,
            attempts=cadastral_limits.attempts,
            request_delay=cadastral_limits.request_delay_seconds,
            row_timeout_seconds=cadastral_limits.row_timeout_seconds,
            stage_timeout_seconds=cadastral_limits.stage_timeout_seconds,
        ).run(MAX_OBJECT_BATCH_SIZE, apply=True),
        logger=logger,
    )
    notifier = None
    if settings.telegram_notifications_enabled:
        notifier = TelegramNotifier(
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            sheet_url=settings.google_sheet_url,
            timeout_seconds=settings.base.http_timeout_seconds,
            logger=logger,
        )
    return MonitoringService(
        pipeline,
        notifier=notifier,
        manual_run=settings.manual_run,
        interval_minutes=settings.monitor_interval_minutes,
        stop_event=stop_event,
        logger=logger,
    )


def main() -> int:
    try:
        settings = MonitoringSettings.from_env()
        giga_settings = GigaChatSettings.from_env()
        cadastral_limits = CadastralLimits.from_env()
    except (ConfigurationError, GigaChatError, ValueError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    logger = configure_safe_logging(
        str(settings.base.log_file),
        secrets=(
            settings.base.apps_script_secret,
            settings.telegram_bot_token,
            giga_settings.auth_key,
        ),
    )
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("TG-09: получен сигнал остановки %s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    lock = SingleInstanceLock(Path("logs/monitoring.lock"))
    if not lock.acquire():
        logger.error("TG-09: другой экземпляр сервиса уже запущен")
        return 1
    try:
        service = build_service(
            settings,
            logger=logger,
            stop_event=stop_event,
            cadastral_limits=cadastral_limits,
        )
        return service.run()
    except Exception as exc:
        logger.warning("TG-09: запуск не выполнен: %s", type(exc).__name__)
        return 1
    finally:
        lock.release()
