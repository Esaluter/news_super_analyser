from __future__ import annotations

import pytest

from news_super_analyser.config import ConfigurationError, Settings


def configure_required(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_APPS_SCRIPT_URL",
        "https://script.google.com/macros/s/deployment/exec",
    )
    monkeypatch.setenv("GOOGLE_APPS_SCRIPT_SECRET", "test-secret")
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "10")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "20")


def test_apps_script_settings_are_loaded_without_service_account(monkeypatch):
    configure_required(monkeypatch)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("SOURCE_SPREADSHEET_ID", raising=False)
    monkeypatch.delenv("TARGET_SPREADSHEET_ID", raising=False)

    settings = Settings.from_env()

    assert settings.apps_script_secret == "test-secret"
    assert settings.sync_interval_minutes == 10
    assert settings.http_timeout_seconds == 20


@pytest.mark.parametrize(
    "url",
    (
        "http://script.google.com/macros/s/deployment/exec",
        "https://example.com/macros/s/deployment/exec",
        "https://script.google.com/macros/s/deployment/dev",
    ),
)
def test_unsafe_or_non_deployment_url_is_rejected(monkeypatch, url):
    configure_required(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPS_SCRIPT_URL", url)

    with pytest.raises(ConfigurationError, match="GOOGLE_APPS_SCRIPT_URL"):
        Settings.from_env()

