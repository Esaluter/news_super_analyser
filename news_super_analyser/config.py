"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"


def load_project_environment() -> None:
    """Load the project-root .env without overriding host environment values."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)


class ConfigurationError(ValueError):
    """Raised when required application configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    apps_script_url: str
    apps_script_secret: str
    sync_interval_minutes: float = 10.0
    http_timeout_seconds: float = 20.0
    log_file: Path = Path("logs/news_super_analyser.log")

    @classmethod
    def from_env(cls) -> "Settings":
        load_project_environment()

        url = os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip()
        secret = os.getenv("GOOGLE_APPS_SCRIPT_SECRET", "").strip()
        missing = [
            name
            for name, value in (
                ("GOOGLE_APPS_SCRIPT_URL", url),
                ("GOOGLE_APPS_SCRIPT_SECRET", secret),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Не заданы обязательные параметры: " + ", ".join(missing)
            )
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.netloc != "script.google.com":
            raise ConfigurationError(
                "GOOGLE_APPS_SCRIPT_URL должен быть HTTPS URL веб-приложения script.google.com"
            )
        if not parsed_url.path.endswith("/exec"):
            raise ConfigurationError("GOOGLE_APPS_SCRIPT_URL должен заканчиваться на /exec")

        raw_interval = os.getenv("SYNC_INTERVAL_MINUTES", "10").strip()
        try:
            interval = float(raw_interval)
        except ValueError as exc:
            raise ConfigurationError("SYNC_INTERVAL_MINUTES должен быть числом") from exc
        if interval <= 0:
            raise ConfigurationError("SYNC_INTERVAL_MINUTES должен быть больше нуля")

        raw_timeout = os.getenv("HTTP_TIMEOUT_SECONDS", "20").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS должен быть числом") from exc
        if timeout <= 0:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS должен быть больше нуля")

        return cls(
            apps_script_url=url,
            apps_script_secret=secret,
            sync_interval_minutes=interval,
            http_timeout_seconds=timeout,
            log_file=Path(
                os.getenv("LOG_FILE", "logs/news_super_analyser.log").strip()
            ).expanduser(),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} должен быть true или false")


def cadastre_mode_from_env() -> str:
    mode = os.getenv("CADASTRE_MODE", "sheets").strip().casefold()
    if mode not in {"sheets", "direct"}:
        raise ConfigurationError("CADASTRE_MODE должен быть равен sheets или direct")
    return mode


@dataclass(frozen=True)
class MonitoringSettings:
    """Configuration used by the autonomous TG-09 entry point."""

    base: Settings
    manual_run: bool
    monitor_interval_minutes: int
    telegram_notifications_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str
    google_sheet_url: str
    cadastre_mode: str

    @classmethod
    def from_env(cls) -> "MonitoringSettings":
        base = Settings.from_env()
        try:
            interval = int(os.getenv("MONITOR_INTERVAL_MINUTES", "60").strip())
        except ValueError as exc:
            raise ConfigurationError(
                "MONITOR_INTERVAL_MINUTES должен быть положительным целым числом"
            ) from exc
        if interval <= 0:
            raise ConfigurationError(
                "MONITOR_INTERVAL_MINUTES должен быть положительным целым числом"
            )

        enabled = _env_bool("TELEGRAM_NOTIFICATIONS_ENABLED", True)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        sheet_url = os.getenv("GOOGLE_SHEET_URL", "").strip()
        missing = []
        if enabled:
            if not token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not chat_id:
                missing.append("TELEGRAM_CHAT_ID")
        if not sheet_url:
            missing.append("GOOGLE_SHEET_URL")
        if missing:
            raise ConfigurationError(
                "Не заданы обязательные параметры TG-09: " + ", ".join(missing)
            )
        parsed = urlparse(sheet_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("GOOGLE_SHEET_URL должен быть HTTPS URL")

        cadastre_mode = cadastre_mode_from_env()

        return cls(
            base=base,
            manual_run=_env_bool("MANUAL_RUN", True),
            monitor_interval_minutes=interval,
            telegram_notifications_enabled=enabled,
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            google_sheet_url=sheet_url,
            cadastre_mode=cadastre_mode,
        )

