from __future__ import annotations

from news_super_analyser import config
from news_super_analyser.config import MonitoringSettings
from news_super_analyser.gigachat_client import GigaChatSettings


ENV_NAMES = (
    "GOOGLE_APPS_SCRIPT_URL",
    "GOOGLE_APPS_SCRIPT_SECRET",
    "GIGACHAT_AUTH_KEY",
    "GOOGLE_SHEET_URL",
    "TELEGRAM_NOTIFICATIONS_ENABLED",
    "MANUAL_RUN",
    "MONITOR_INTERVAL_MINUTES",
)


def test_project_env_is_loaded_when_vscode_working_directory_is_elsewhere(
    monkeypatch, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    env_file = project / ".env"
    env_file.write_text(
        "\n".join(
            (
                "GOOGLE_APPS_SCRIPT_URL=https://script.google.com/macros/s/offline/exec",
                "GOOGLE_APPS_SCRIPT_SECRET=offline-apps-value",
                "GIGACHAT_AUTH_KEY=offline-gigachat-value",
                "GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/offline/edit",
                "TELEGRAM_NOTIFICATIONS_ENABLED=false",
                "MANUAL_RUN=true",
                "MONITOR_INTERVAL_MINUTES=60",
            )
        ),
        encoding="utf-8",
    )
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", env_file)
    elsewhere = tmp_path / "vscode-working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    monitoring = MonitoringSettings.from_env()
    gigachat = GigaChatSettings.from_env()

    assert monitoring.manual_run is True
    assert monitoring.base.apps_script_secret
    assert gigachat.auth_key


def test_bothost_environment_has_priority_over_optional_env_file(
    monkeypatch, tmp_path
):
    env_file = tmp_path / ".env"
    env_file.write_text("GIGACHAT_AUTH_KEY=file-placeholder\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ENV_FILE", env_file)
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "host-placeholder")

    settings = GigaChatSettings.from_env()

    assert settings.auth_key == "host-placeholder"
