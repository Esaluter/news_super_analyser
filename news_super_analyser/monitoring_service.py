"""Scheduling and graceful shutdown for TG-09."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Protocol

from .pipeline import CycleReport
from .telegram_notify import TelegramNotificationError


class Pipeline(Protocol):
    def run_cycle(self) -> CycleReport: ...


class Notifier(Protocol):
    def send(self, report: CycleReport) -> None: ...


class MonitoringService:
    def __init__(
        self,
        pipeline: Pipeline,
        *,
        notifier: Notifier | None,
        manual_run: bool,
        interval_minutes: int,
        stop_event: threading.Event | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.notifier = notifier
        self.manual_run = manual_run
        self.interval_seconds = interval_minutes * 60
        self.stop_event = stop_event or threading.Event()
        self.logger = logger or logging.getLogger(__name__)

    def run(self) -> int:
        while not self.stop_event.is_set():
            report = self._one_cycle()
            if self.manual_run:
                return 1 if report.errors else 0
            self.logger.info(
                "TG-09: следующий цикл через %d минут",
                self.interval_seconds // 60,
            )
            if self.stop_event.wait(self.interval_seconds):
                break
        self.logger.info("TG-09: сервис корректно остановлен")
        return 0

    def _one_cycle(self) -> CycleReport:
        try:
            report = self.pipeline.run_cycle()
        except Exception as exc:
            from datetime import datetime

            self.logger.warning(
                "TG-09: непредвиденная ошибка цикла %s", type(exc).__name__
            )
            report = CycleReport(finished_at=datetime.now(), errors=1)
        if self.notifier is not None:
            report.next_check_at = (
                None
                if self.manual_run
                else report.finished_at + timedelta(seconds=self.interval_seconds)
            )
            try:
                self.notifier.send(report)
            except Exception as exc:
                diagnostic = (
                    str(exc)
                    if isinstance(exc, TelegramNotificationError)
                    else type(exc).__name__
                )
                self.logger.warning(
                    "TG-09: Telegram-уведомление не отправлено: %s",
                    diagnostic,
                )
        return report
