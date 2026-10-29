from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

import pytest

from news_super_analyser.monitoring_service import MonitoringService
from news_super_analyser.pipeline import CycleAlreadyRunning, CycleReport, MonitoringPipeline
from news_super_analyser.telegram_notify import TelegramNotificationError


@dataclass
class Result:
    new_rows: int = 0
    errors: int = 0
    fetch_failures: int = 0
    model_errors: int = 0
    write_errors: int = 0
    cadastral_numbers_found: int = 0


@dataclass
class Item:
    status: str
    objects: int = 0


def pipeline(**overrides):
    calls = []

    def stage(name, value):
        def run():
            calls.append(name)
            if isinstance(value, Exception):
                raise value
            return value

        return run

    values = {
        "sync": Result(new_rows=2),
        "full_text": Result(),
        "gemini": Result(),
        "giga_text": Result(),
        "analysis": [Item("success", 3)],
        "cadastral": Result(cadastral_numbers_found=2),
    }
    values.update(overrides)
    return (
        MonitoringPipeline(
            **{name: stage(name, value) for name, value in values.items()},
            now=lambda: datetime(2026, 10, 28, 14, 30),
        ),
        calls,
    )


def test_complete_successful_cycle():
    subject, calls = pipeline()
    report = subject.run_cycle()
    assert calls == ["sync", "full_text", "gemini", "giga_text", "analysis", "cadastral"]
    assert (report.new_news, report.analyzed_news, report.processed_objects) == (2, 1, 3)
    assert report.cadastral_numbers_found == 2
    assert report.errors == 0
    assert report.status == "успешно"


def test_no_new_news_can_still_process_backlog():
    subject, _ = pipeline(sync=Result(), analysis=[Item("success", 2)])
    report = subject.run_cycle()
    assert report.new_news == 0
    assert report.analyzed_news == 1
    assert report.processed_objects == 2


def test_collection_failure_does_not_block_old_rows():
    subject, calls = pipeline(sync=RuntimeError("offline"))
    report = subject.run_cycle()
    assert report.analyzed_news == 1
    assert "analysis" in calls
    assert report.status == "частично успешно"


@pytest.mark.parametrize(
    "failed_stage",
    ["full_text", "gemini", "giga_text", "analysis", "cadastral"],
)
def test_independent_stage_failure_continues(failed_stage):
    subject, calls = pipeline(**{failed_stage: RuntimeError("secret detail")})
    report = subject.run_cycle()
    assert calls[-1] == "cadastral"
    assert report.errors == 1


def test_internal_model_and_write_errors_are_counted():
    subject, _ = pipeline(giga_text=Result(model_errors=1, write_errors=2))
    assert subject.run_cycle().errors == 3


def test_failed_analysis_item_is_not_counted_as_analyzed():
    subject, _ = pipeline(analysis=[Item("error", 7)])
    report = subject.run_cycle()
    assert report.analyzed_news == 0
    assert report.processed_objects == 0
    assert report.errors == 1


def test_overlap_is_rejected():
    entered = threading.Event()
    release = threading.Event()

    def blocking_sync():
        entered.set()
        release.wait(2)
        return Result()

    subject, _ = pipeline(sync=Result())
    subject._stages = (("сбор новостей", blocking_sync),)
    worker = threading.Thread(target=subject.run_cycle)
    worker.start()
    assert entered.wait(1)
    with pytest.raises(CycleAlreadyRunning):
        subject.run_cycle()
    release.set()
    worker.join(2)


class FakePipeline:
    def __init__(self, reports):
        self.reports = list(reports)
        self.calls = 0

    def run_cycle(self):
        self.calls += 1
        return self.reports.pop(0)


class FakeNotifier:
    def __init__(self, fail=False):
        self.reports = []
        self.fail = fail

    def send(self, report):
        self.reports.append(report)
        if self.fail:
            raise RuntimeError("telegram down")


class StopAfterWait:
    def __init__(self):
        self.waits = []

    def is_set(self):
        return False

    def wait(self, seconds):
        self.waits.append(seconds)
        return True


def report(errors=0):
    return CycleReport(datetime(2026, 10, 28, 14, 30), errors=errors)


def test_manual_mode_runs_exactly_once_and_notifies():
    pipe = FakePipeline([report()])
    notifier = FakeNotifier()
    service = MonitoringService(pipe, notifier=notifier, manual_run=True, interval_minutes=60)
    assert service.run() == 0
    assert pipe.calls == 1
    assert len(notifier.reports) == 1
    assert notifier.reports[0].next_check_at is None


def test_manual_mode_returns_failure_for_partial_cycle():
    service = MonitoringService(
        FakePipeline([report(1)]), notifier=None, manual_run=True, interval_minutes=60
    )
    assert service.run() == 1


def test_automatic_mode_waits_after_completed_cycle():
    stop = StopAfterWait()
    pipe = FakePipeline([report()])
    notifier = FakeNotifier()
    service = MonitoringService(
        pipe, notifier=notifier, manual_run=False, interval_minutes=7, stop_event=stop
    )
    assert service.run() == 0
    assert pipe.calls == 1
    assert stop.waits == [420]
    assert notifier.reports[0].next_check_at == datetime(2026, 10, 28, 14, 37)


def test_telegram_error_does_not_repeat_cycle():
    pipe = FakePipeline([report()])
    service = MonitoringService(
        pipe, notifier=FakeNotifier(fail=True), manual_run=True, interval_minutes=60
    )
    assert service.run() == 0
    assert pipe.calls == 1


def test_safe_telegram_api_diagnostic_is_written_to_log(caplog):
    class DiagnosticNotifier:
        def send(self, report):
            raise TelegramNotificationError(
                "http_status=403; error_code=403; description=Forbidden"
            )

    pipe = FakePipeline([report()])
    with caplog.at_level("WARNING"):
        service = MonitoringService(
            pipe,
            notifier=DiagnosticNotifier(),
            manual_run=True,
            interval_minutes=60,
        )
        assert service.run() == 0
    assert "http_status=403" in caplog.text
    assert "description=Forbidden" in caplog.text


def test_disabled_notification_is_supported():
    service = MonitoringService(
        FakePipeline([report()]), notifier=None, manual_run=True, interval_minutes=60
    )
    assert service.run() == 0


def test_cadastral_timeout_still_produces_one_partial_telegram_report():
    pipe, _ = pipeline(cadastral=Result(errors=2))
    notifier = FakeNotifier()
    service = MonitoringService(
        pipe, notifier=notifier, manual_run=True, interval_minutes=60
    )

    assert service.run() == 1
    assert len(notifier.reports) == 1
    assert notifier.reports[0].errors == 2
    assert notifier.reports[0].safe_error_summary == "кадастровое обогащение"
