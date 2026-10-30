from __future__ import annotations

import json
import ssl
import subprocess

import pytest

from news_super_analyser.cadastral_enrichment import (
    CadastralLimits,
    NspdDeadlineExceeded,
    PynspdCadastralSearcher,
)
from news_super_analyser.nspd_worker import _verified_client


def test_worker_client_enforces_tls_and_disables_internal_retries(monkeypatch):
    monkeypatch.setenv("PYNSPD_CLIENT_TIMEOUT", "999")
    monkeypatch.setenv("PYNSPD_CLIENT_RETRIES", "3")

    client = _verified_client(2.1)
    try:
        import pynspd._sync.api as pynspd_sync

        assert client._timeout == 3
        assert client._retries == 0
        assert pynspd_sync.SSL_CONTEXT.verify_mode == ssl.CERT_REQUIRED
        assert pynspd_sync.SSL_CONTEXT.check_hostname is True
    finally:
        client.close()


def test_cadastral_limits_are_configurable_without_pynspd_variables(monkeypatch):
    monkeypatch.setenv("NSPD_CLIENT_TIMEOUT", "8")
    monkeypatch.setenv("NSPD_ROW_TIMEOUT_SECONDS", "24")
    monkeypatch.setenv("NSPD_STAGE_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("NSPD_ATTEMPTS", "1")
    monkeypatch.setenv("NSPD_REQUEST_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("PYNSPD_CLIENT_TIMEOUT", "999")

    limits = CadastralLimits.from_env()

    assert limits.http_timeout_seconds == 8
    assert limits.row_timeout_seconds == 24
    assert limits.stage_timeout_seconds == 90
    assert limits.attempts == 1
    assert limits.request_delay_seconds == 0.5


def test_searcher_uses_one_killable_worker_and_sanitized_environment(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "candidates": [
                        {
                            "cadastral_number": "31:16:1:1",
                            "address": "Белгород, ул Садовая, д 5",
                            "category_name": "Здание",
                            "building_name": "Летняя кухня",
                            "purpose": "Нежилое здание",
                            "permitted_use": "Размещение здания",
                            "cadastral_quarter": "31:16:1",
                        }
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setenv("PYNSPD_CLIENT_TIMEOUT", "999")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "must-not-enter-worker")
    searcher = PynspdCadastralSearcher(timeout=10, run=run)

    result = searcher.search(
        "query", region="", locality="", address="", timeout_seconds=7
    )

    assert result[0]["cadastral_number"] == "31:16:1:1"
    assert result[0]["category_name"] == "Здание"
    assert result[0]["building_name"] == "Летняя кухня"
    assert result[0]["cadastral_quarter"] == "31:16:1"
    assert captured["timeout"] == 7
    assert "PYNSPD_CLIENT_TIMEOUT" not in captured["env"]
    assert "GIGACHAT_AUTH_KEY" not in captured["env"]
    assert captured["command"][-1] == "news_super_analyser.nspd_worker"


def test_searcher_kills_worker_at_hard_timeout():
    def run(_command, **kwargs):
        raise subprocess.TimeoutExpired("worker", kwargs["timeout"])

    searcher = PynspdCadastralSearcher(timeout=10, run=run)

    with pytest.raises(NspdDeadlineExceeded) as caught:
        searcher.search(
            "query",
            region="",
            locality="",
            address="",
            timeout_seconds=4,
            timeout_reason="stage_timeout",
        )

    assert caught.value.reason == "stage_timeout"
