from __future__ import annotations

import logging
import threading
from pathlib import Path

from news_super_analyser import monitoring_app
from news_super_analyser.cadastral_enrichment import CadastralLimits
from news_super_analyser.config import MonitoringSettings, Settings


def settings(mode):
    return MonitoringSettings(
        base=Settings(
            apps_script_url="https://script.google.com/macros/s/test/exec",
            apps_script_secret="secret",
            http_timeout_seconds=1,
            log_file=Path("logs/test.log"),
        ),
        manual_run=True,
        monitor_interval_minutes=60,
        telegram_notifications_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        google_sheet_url="https://docs.google.com/spreadsheets/d/test/edit",
        cadastre_mode=mode,
    )


def test_sheets_mode_builds_only_queue_stage(monkeypatch):
    calls = []

    class Queue:
        def __init__(self, *_args, **_kwargs):
            calls.append("queue_init")

        def run(self):
            calls.append("queue_run")
            return object()

    class ForbiddenDirect:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("direct NSPD must not be created in sheets mode")

    monkeypatch.setattr(monitoring_app, "CadastralQueueSynchronizer", Queue)
    monkeypatch.setattr(monitoring_app, "PynspdCadastralSearcher", ForbiddenDirect)
    monkeypatch.setattr(
        monitoring_app.GigaChatSettings, "from_env", classmethod(lambda cls: object())
    )

    service = monitoring_app.build_service(
        settings("sheets"),
        logger=logging.getLogger("test"),
        stop_event=threading.Event(),
        cadastral_limits=None,
    )
    service.pipeline._stages[-1][1]()

    assert calls == ["queue_init", "queue_run"]
    assert service.pipeline.cadastre_mode == "sheets"


def test_direct_mode_keeps_existing_processor_and_does_not_use_queue(monkeypatch):
    calls = []

    class ForbiddenQueue:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("queue must not be created in direct mode")

    class Searcher:
        def __init__(self, *, timeout):
            calls.append(("searcher", timeout))

    class Processor:
        def __init__(self, _gateway, _searcher, **_kwargs):
            calls.append("processor")

        def run(self, limit, *, apply):
            calls.append(("run", limit, apply))
            return object()

    monkeypatch.setattr(monitoring_app, "CadastralQueueSynchronizer", ForbiddenQueue)
    monkeypatch.setattr(monitoring_app, "PynspdCadastralSearcher", Searcher)
    monkeypatch.setattr(monitoring_app, "CadastralEnrichmentProcessor", Processor)
    monkeypatch.setattr(
        monitoring_app.GigaChatSettings, "from_env", classmethod(lambda cls: object())
    )
    limits = CadastralLimits()

    service = monitoring_app.build_service(
        settings("direct"),
        logger=logging.getLogger("test"),
        stop_event=threading.Event(),
        cadastral_limits=limits,
    )
    service.pipeline._stages[-1][1]()

    assert calls == [
        ("searcher", limits.http_timeout_seconds),
        "processor",
        ("run", monitoring_app.MAX_OBJECT_BATCH_SIZE, True),
    ]
    assert service.pipeline.cadastre_mode == "direct"
