from __future__ import annotations

import logging
import io
from datetime import datetime

import pytest
import requests

from news_super_analyser.config import ConfigurationError, MonitoringSettings
from news_super_analyser.pipeline import CycleReport, StageOutcome
from news_super_analyser.safe_logging import (
    RedactingFilter,
    RedactingFormatter,
    SecretRedactor,
)
from news_super_analyser.telegram_notify import (
    TelegramNotificationError,
    TelegramNotifier,
    format_cycle_message,
)


BASE_ENV = {
    "GOOGLE_APPS_SCRIPT_URL": "https://script.google.com/macros/s/example/exec",
    "GOOGLE_APPS_SCRIPT_SECRET": "apps-secret",
    "GOOGLE_SHEET_URL": "https://docs.google.com/spreadsheets/d/example/edit",
    "TELEGRAM_BOT_TOKEN": "123:token",
    "TELEGRAM_CHAT_ID": "42",
    "TELEGRAM_NOTIFICATIONS_ENABLED": "true",
    "GIGACHAT_AUTH_KEY": "giga-secret",
}


def set_env(monkeypatch, **changes):
    values = {**BASE_ENV, **changes}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_monitoring_settings_read_environment(monkeypatch):
    set_env(monkeypatch, MANUAL_RUN="false", MONITOR_INTERVAL_MINUTES="15")
    settings = MonitoringSettings.from_env()
    assert settings.manual_run is False
    assert settings.monitor_interval_minutes == 15
    assert settings.telegram_notifications_enabled is True


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "hour"])
def test_invalid_interval_is_rejected(monkeypatch, value):
    set_env(monkeypatch, MONITOR_INTERVAL_MINUTES=value)
    with pytest.raises(ConfigurationError):
        MonitoringSettings.from_env()


def test_invalid_boolean_is_rejected(monkeypatch):
    set_env(monkeypatch, MANUAL_RUN="sometimes")
    with pytest.raises(ConfigurationError):
        MonitoringSettings.from_env()


def test_telegram_credentials_optional_when_notifications_disabled(monkeypatch):
    set_env(
        monkeypatch,
        TELEGRAM_NOTIFICATIONS_ENABLED="false",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHAT_ID="",
    )
    settings = MonitoringSettings.from_env()
    assert settings.telegram_notifications_enabled is False


def sample_report(errors=0):
    return CycleReport(
        datetime(2026, 10, 28, 14, 30),
        new_news=0,
        analyzed_news=1,
        processed_objects=2,
        cadastral_numbers_found=1,
        errors=errors,
        outcomes=[StageOutcome("GigaChat", errors == 0, errors)],
    )


def test_zero_new_news_report_contains_actual_old_work_and_link():
    message = format_cycle_message(
        sample_report(), "https://docs.google.com/spreadsheets/d/example/edit"
    )
    assert "Новых новостей: <b>0</b>" in message
    assert "Проанализировано: <b>1</b>" in message
    assert "Следующая проверка: <b>не запланирована (ручной режим)</b>" in message
    assert 'href="https://docs.google.com/' in message


def test_automatic_report_contains_planned_next_check():
    report = sample_report()
    report.next_check_at = datetime(2026, 10, 28, 15, 30)
    message = format_cycle_message(report, "https://example.test/sheet")
    assert "Следующая проверка: <b>28.10.2026, 15:30</b>" in message


def test_next_check_can_cross_midnight():
    report = sample_report(1)
    report.finished_at = datetime(2026, 10, 28, 23, 45)
    report.next_check_at = datetime(2026, 10, 29, 0, 45)
    message = format_cycle_message(report, "https://example.test/sheet")
    assert "Следующая проверка: <b>29.10.2026, 00:45</b>" in message
    assert "частично успешно" in message


def test_google_sheet_link_is_valid_html_and_dynamic_text_is_escaped():
    report = sample_report(1)
    report.outcomes = [StageOutcome("этап <опасный>", False, 1)]
    message = format_cycle_message(
        report, "https://docs.google.com/spreadsheets/d/example/edit?gid=1&range=A1"
    )
    assert "этап &lt;опасный&gt;" in message
    assert "gid=1&amp;range=A1" in message


def test_partial_report_has_safe_stage_description():
    message = format_cycle_message(sample_report(1), "https://example.test/sheet")
    assert "частично успешно" in message
    assert "GigaChat" in message


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"ok": True, "result": {}}

    def json(self):
        return self.payload


def test_successful_telegram_notification_uses_timeout_and_one_request():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    TelegramNotifier(
        token="secret-token",
        chat_id="42",
        sheet_url="https://example.test/sheet",
        timeout_seconds=9,
        post=post,
    ).send(sample_report())
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 9
    assert calls[0][1]["data"]["parse_mode"] == "HTML"
    assert calls[0][1]["data"]["chat_id"] == "42"
    assert "<a href=" in calls[0][1]["data"]["text"]


def test_telegram_http_error_is_sanitized():
    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="42",
        sheet_url="https://example.test/sheet",
        post=lambda *args, **kwargs: Response(
            500,
            {
                "ok": False,
                "error_code": 500,
                "description": "Internal Server Error",
            },
        ),
    )
    with pytest.raises(TelegramNotificationError) as caught:
        notifier.send(sample_report())
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    "status,description",
    [
        (400, "Bad Request: chat not found"),
        (401, "Unauthorized"),
        (403, "Forbidden: bot is not a member of the channel chat"),
    ],
)
def test_telegram_api_error_preserves_safe_code_and_description(status, description):
    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="-1001234567890",
        sheet_url="https://example.test/sheet",
        post=lambda *args, **kwargs: Response(
            status,
            {"ok": False, "error_code": status, "description": description},
        ),
    )
    with pytest.raises(TelegramNotificationError) as caught:
        notifier.send(sample_report())
    message = str(caught.value)
    assert f"http_status={status}" in message
    assert f"error_code={status}" in message
    assert description in message
    assert "secret-token" not in message


def test_telegram_429_reports_safe_retry_after_without_retrying():
    calls = 0

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Response(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 17",
                "parameters": {"retry_after": 17},
            },
        )

    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="-1001234567890",
        sheet_url="https://example.test/sheet",
        post=post,
    )
    with pytest.raises(TelegramNotificationError, match="retry_after=17s"):
        notifier.send(sample_report())
    assert calls == 1


def test_channel_chat_id_is_passed_unchanged_without_chat_type_restriction():
    payloads = []

    def post(_url, **kwargs):
        payloads.append(kwargs["data"])
        return Response()

    TelegramNotifier(
        token="secret-token",
        chat_id="-1001234567890",
        sheet_url="https://example.test/sheet",
        post=post,
    ).send(sample_report())
    assert payloads[0]["chat_id"] == "-1001234567890"


def test_telegram_description_redacts_token_and_full_url():
    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="42",
        sheet_url="https://example.test/sheet",
        post=lambda *args, **kwargs: Response(
            400,
            {
                "ok": False,
                "error_code": 400,
                "description": (
                    "Bad Request secret-token "
                    "https://api.telegram.org/botsecret-token/sendMessage"
                ),
            },
        ),
    )
    with pytest.raises(TelegramNotificationError) as caught:
        notifier.send(sample_report())
    assert "secret-token" not in str(caught.value)
    assert "api.telegram.org" not in str(caught.value)


def test_telegram_transport_error_is_sanitized():
    def fail(*args, **kwargs):
        raise requests.Timeout("https://api.telegram.org/botsecret-token/sendMessage")

    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="42",
        sheet_url="https://example.test/sheet",
        post=fail,
    )
    with pytest.raises(TelegramNotificationError) as caught:
        notifier.send(sample_report())
    assert "secret-token" not in str(caught.value)


def test_secret_redactor_removes_config_and_authorization_values():
    redactor = SecretRedactor(("apps-secret", "telegram-secret", "giga-secret"))
    raw = (
        "apps-secret telegram-secret giga-secret Authorization: Bearer abc.def "
        "https://api.telegram.org/bot123:xyz/sendMessage"
    )
    cleaned = redactor.redact(raw)
    assert "apps-secret" not in cleaned
    assert "telegram-secret" not in cleaned
    assert "giga-secret" not in cleaned
    assert "abc.def" not in cleaned
    assert "123:xyz" not in cleaned


def test_logging_filter_and_traceback_do_not_leak_secrets():
    stream = io.StringIO()
    redactor = SecretRedactor(("super-secret",))
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(redactor))
    handler.setFormatter(RedactingFormatter("%(message)s", redactor))
    logger = logging.getLogger("tg09-secret-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        raise RuntimeError("super-secret")
    except RuntimeError:
        logger.exception("Bearer hidden-token super-secret")
    output = stream.getvalue()
    assert "super-secret" not in output
    assert "hidden-token" not in output
    assert "[REDACTED]" in output


def test_entrypoint_imports():
    import run
    from news_super_analyser import monitoring_app

    assert callable(run.main)
    assert callable(monitoring_app.main)
