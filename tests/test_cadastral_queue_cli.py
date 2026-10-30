from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from news_super_analyser import cadastral_queue_cli
from news_super_analyser.cadastral_enrichment import CadastralLimits
from news_super_analyser.cadastral_queue import LocalQueueStats


OBJECT_ID = "123e4567-e89b-42d3-a456-426614174000|1"


def _task(status="НАЙДЕНО"):
    return {
        "object_id": OBJECT_ID,
        "region": "Регион",
        "locality": "Город",
        "address": "ул. Новая, д. 1",
        "cadastral_number": "31:16:0101001:25",
        "cadastral_quarter": "31:16:0101001",
        "all_cadastral": "31:16:0101001:25",
        "status": status,
        "error": "",
        "checked_at": "30.10.2026 10:00:00",
    }


def test_object_id_requires_debug_flag():
    with pytest.raises(SystemExit) as caught:
        cadastral_queue_cli.main(["--object-id", OBJECT_ID])
    assert caught.value.code == 2


def test_empty_prompt_cannot_fall_back_to_queue_processing():
    with pytest.raises(SystemExit) as caught:
        cadastral_queue_cli.main(["--debug-nspd", "--object-id", "   "])
    assert caught.value.code == 2


def test_single_completed_object_is_diagnostic_only():
    class Gateway:
        def read_cadastral_task(self, object_id):
            assert object_id == OBJECT_ID
            return _task("НЕОДНОЗНАЧНО")

        def update_cadastral_task(self, **_values):
            raise AssertionError("must not write")

    class Processor:
        def diagnose_task(self, task):
            assert task["status"] == "НЕОДНОЗНАЧНО"
            return SimpleNamespace(status="НАЙДЕНО")

    assert (
        cadastral_queue_cli._diagnose_single_object(
            OBJECT_ID, Gateway(), Processor(), None
        )
        == 0
    )


def test_unknown_object_makes_no_nspd_request():
    class Gateway:
        def read_cadastral_task(self, _object_id):
            return None

    class Processor:
        def diagnose_task(self, _task):
            raise AssertionError("NSPD must not be called")

    assert (
        cadastral_queue_cli._diagnose_single_object(
            OBJECT_ID, Gateway(), Processor(), None
        )
        == 2
    )


def test_normal_cli_does_not_construct_debug_recorder(monkeypatch):
    settings = SimpleNamespace(
        apps_script_url="https://script.google.com/test/exec",
        apps_script_secret="secret",
        http_timeout_seconds=10,
    )
    limits = CadastralLimits(
        http_timeout_seconds=10,
        row_timeout_seconds=30,
        stage_timeout_seconds=120,
        attempts=1,
        request_delay_seconds=0,
    )
    monkeypatch.setattr(
        cadastral_queue_cli.Settings, "from_env", lambda: settings
    )
    monkeypatch.setattr(
        cadastral_queue_cli.CadastralLimits, "from_env", lambda: limits
    )
    monkeypatch.setattr(
        cadastral_queue_cli,
        "configure_safe_logging",
        lambda *_args, **_kwargs: logging.getLogger("test.cli"),
    )
    monkeypatch.setattr(
        cadastral_queue_cli, "AppsScriptGateway", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        cadastral_queue_cli,
        "VerifiedNspdHttpSearcher",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        cadastral_queue_cli,
        "NspdDebugRecorder",
        lambda *_args, **_kwargs: pytest.fail("debug recorder must stay disabled"),
    )

    class Processor:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, limit):
            assert limit == 1
            return LocalQueueStats(total_records=1, checked=1, found=1)

    monkeypatch.setattr(cadastral_queue_cli, "LocalCadastralQueueProcessor", Processor)

    assert cadastral_queue_cli.main(["--limit", "1"]) == 0
