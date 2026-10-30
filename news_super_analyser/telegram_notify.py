"""One safe Telegram Bot API summary per completed monitoring cycle."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .pipeline import CycleReport


DISPLAY_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")


class TelegramNotificationError(RuntimeError):
    """Sanitized Telegram failure safe to include in application logs."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        error_code: int | None = None,
        description: str = "",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after


def format_cycle_message(report: CycleReport, sheet_url: str) -> str:
    finished = _display_datetime(report.finished_at).strftime("%d.%m.%Y, %H:%M")
    next_check = (
        _display_datetime(report.next_check_at).strftime("%d.%m.%Y, %H:%M")
        if report.next_check_at is not None
        else "не запланирована (ручной режим)"
    )
    lines = [
        "<b>🦝 Супер анализатор новостей</b>",
        "",
        f"Проверка завершена: {html.escape(finished)}",
        "",
        f"📰 Новых новостей: <b>{report.new_news}</b>",
        f"🔍 Проанализировано: <b>{report.analyzed_news}</b>",
        f"🏢 Обработано объектов: <b>{report.processed_objects}</b>",
    ]
    if report.cadastre_mode == "direct":
        lines.append(
            f"📍 Найдено кадастровых номеров: <b>{report.cadastral_numbers_found}</b>"
        )
    else:
        lines.extend(
            (
                f"🗂️ Кадастровых записей добавлено: <b>{report.cadastral_records_added}</b>",
                f"📥 Кадастровых результатов получено: <b>{report.cadastral_results_received}</b>",
            )
        )
    lines.extend(
        (
            f"⚠️ Ошибок: <b>{report.errors}</b>",
            f"🕒 Следующая проверка: <b>{html.escape(next_check)}</b>",
            "",
            f"<b>Статус: {html.escape(report.status)}</b>",
        )
    )
    if report.errors and report.safe_error_summary:
        lines.append("Сбои этапов: " + html.escape(report.safe_error_summary))
    lines.extend(("", f'<a href="{html.escape(sheet_url, quote=True)}">📊 Открыть Google Таблицу</a>'))
    return "\n".join(lines)


def _display_datetime(value: datetime) -> datetime:
    """Convert a server-local or timezone-aware value to Yekaterinburg time."""
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(DISPLAY_TIMEZONE)


class TelegramNotifier:
    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        sheet_url: str,
        timeout_seconds: float = 20.0,
        post: Callable[..., Any] = requests.post,
        logger: logging.Logger | None = None,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self.sheet_url = sheet_url
        self.timeout_seconds = timeout_seconds
        self._post = post
        self.logger = logger or logging.getLogger(__name__)

    def send(self, report: CycleReport) -> None:
        # The token must be in the Bot API URL, so this URL is never logged.
        endpoint = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            response = self._post(
                endpoint,
                data={
                    "chat_id": self._chat_id,
                    "text": format_cycle_message(report, self.sheet_url),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TelegramNotificationError(
                f"transport={type(exc).__name__}"
            ) from exc
        status = int(getattr(response, "status_code", 0))
        payload = _response_json(response)
        api_ok = payload.get("ok") if isinstance(payload, dict) else None
        if status < 200 or status >= 300 or api_ok is False:
            error_code = _optional_int(payload.get("error_code"))
            description = _safe_description(payload.get("description"), self._token)
            parameters = payload.get("parameters")
            retry_after = (
                _optional_int(parameters.get("retry_after"))
                if isinstance(parameters, dict)
                else None
            )
            parts = [f"http_status={status}"]
            if error_code is not None:
                parts.append(f"error_code={error_code}")
            if description:
                parts.append(f"description={description}")
            if retry_after is not None:
                parts.append(f"retry_after={retry_after}s")
            raise TelegramNotificationError(
                "; ".join(parts),
                http_status=status,
                error_code=error_code,
                description=description,
                retry_after=retry_after,
            )
        if api_ok is not True:
            raise TelegramNotificationError(
                f"http_status={status}; description=unexpected Telegram response",
                http_status=status,
                description="unexpected Telegram response",
            )


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_description(value: Any, token: str) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    text = " ".join(str(value).split())
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=:-]+",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"https?://\S+", "[URL]", text, flags=re.I)
    return text[:300]
