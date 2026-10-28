"""Fetch full text from a public Telegram post through its embed page."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import requests


URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
PUBLIC_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
RESERVED_PATHS = {"c", "s", "joinchat", "share", "addstickers", "proxy"}
TEXT_CLASS = "tgme_widget_message_text"
MAX_FULL_TEXT_LENGTH = 50_000


@dataclass(frozen=True)
class TelegramPostResult:
    url: str
    status: str
    text: str = ""
    reason: str | None = None
    method: str = "embed"

    @property
    def successful(self) -> bool:
        return self.status == "loaded" and bool(self.text)


class TelegramMessageTextParser(HTMLParser):
    """Capture only the server-rendered Telegram message body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._div_depth = 0
        self._parts: list[str] = []
        self._texts: list[str] = []
        self.saw_container = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if not self._div_depth and tag == "div" and TEXT_CLASS in classes:
            self.saw_container = True
            self._div_depth = 1
            self._parts = []
            return
        if not self._div_depth:
            return
        if tag == "div":
            self._div_depth += 1
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._parts.append(alt)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._div_depth and tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._div_depth or tag != "div":
            return
        self._div_depth -= 1
        if self._div_depth == 0:
            # Preserve Telegram's internal whitespace and paragraph breaks.
            text = "".join(self._parts).replace("\r\n", "\n").replace("\r", "\n")
            if text.strip():
                self._texts.append(text)
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._div_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return max(self._texts, key=len, default="")


def public_post_parts(url: str) -> tuple[str, str] | None:
    """Return public channel name and message id for a supported URL."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or parts.netloc.lower() not in PUBLIC_HOSTS:
        return None
    path = [segment for segment in parts.path.split("/") if segment]
    if len(path) == 3 and path[0].lower() == "s":
        path = path[1:]
    if len(path) != 2:
        return None
    channel, message_id = path
    if channel.lower() in RESERVED_PATHS:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_]+", channel) or not message_id.isdigit():
        return None
    return channel, message_id


def build_embed_url(url: str) -> str | None:
    parts = public_post_parts(url)
    if parts is None:
        return None
    channel, message_id = parts
    return f"https://t.me/{channel}/{message_id}?embed=1&mode=tme"


def find_public_telegram_url(value: str) -> str | None:
    """Find the first supported public post URL in a Sheets links cell."""
    for match in URL_RE.findall(value):
        candidate = match.rstrip('.,;!?)]}')
        if public_post_parts(candidate):
            return candidate
    return None


class TelegramEmbedClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        min_interval_seconds: float = 1.0,
        get: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self._get = get or requests.get
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_started: float | None = None

    def fetch(self, url: str) -> TelegramPostResult:
        embed_url = build_embed_url(url)
        if embed_url is None:
            return TelegramPostResult(
                url=url, status="unsupported_url", reason="unsupported_public_post_url"
            )

        self._wait_for_rate_limit()
        try:
            response = self._get(
                embed_url,
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; NewsSuperAnalyser-TG02A/1.0; "
                        "+manual-full-text-loader)"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
                allow_redirects=True,
            )
        except requests.Timeout:
            return TelegramPostResult(url=url, status="error", reason="timeout")
        except requests.RequestException as exc:
            return TelegramPostResult(
                url=url,
                status="error",
                reason=f"network_{type(exc).__name__}",
            )

        status_code = int(getattr(response, "status_code", 0))
        if status_code in {401, 403, 404, 410}:
            return TelegramPostResult(
                url=url, status="unavailable", reason=f"http_{status_code}"
            )
        if status_code == 429:
            return TelegramPostResult(url=url, status="error", reason="rate_limited")
        if status_code < 200 or status_code >= 300:
            return TelegramPostResult(
                url=url, status="error", reason=f"http_{status_code}"
            )

        content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
        if content_type and "html" not in content_type.lower():
            return TelegramPostResult(
                url=url, status="error", reason="unexpected_content_type"
            )

        parser = TelegramMessageTextParser()
        try:
            parser.feed(str(response.text))
            parser.close()
        except Exception as exc:
            return TelegramPostResult(
                url=url,
                status="error",
                reason=f"invalid_html_{type(exc).__name__}",
            )
        if not parser.saw_container:
            return TelegramPostResult(
                url=url, status="unavailable", reason="message_text_not_found"
            )
        if not parser.text:
            return TelegramPostResult(
                url=url, status="unavailable", reason="empty_message_text"
            )
        if len(parser.text) > MAX_FULL_TEXT_LENGTH:
            return TelegramPostResult(
                url=url, status="error", reason="text_exceeds_google_cell_limit"
            )
        return TelegramPostResult(url=url, status="loaded", text=parser.text)

    def _wait_for_rate_limit(self) -> None:
        now = self._monotonic()
        if self._last_request_started is not None:
            remaining = self.min_interval_seconds - (now - self._last_request_started)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_started = now

