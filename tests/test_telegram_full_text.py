from __future__ import annotations

from pathlib import Path

import pytest

from news_super_analyser.telegram_full_text import (
    TelegramEmbedClient,
    build_embed_url,
    find_public_telegram_url,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_successful_embed_extraction_preserves_paragraphs_and_excludes_ui():
    requested = []

    def get(url, **kwargs):
        requested.append((url, kwargs))
        return FakeResponse(fixture("telegram_embed_success.html"))

    client = TelegramEmbedClient(get=get, min_interval_seconds=0)
    result = client.fetch("https://t.me/example_channel/123")

    assert result.successful
    assert result.text == (
        "Первый абзац.\n\n"
        "Второй абзац с ссылкой.\n"
        "Третья строка.🙂"
    )
    assert "views" not in result.text
    assert "👍" not in result.text
    assert "advertisement" not in result.text
    assert requested[0][0] == "https://t.me/example_channel/123?embed=1&mode=tme"
    assert requested[0][1]["timeout"] == 20.0


def test_unavailable_post_without_message_container_is_not_successful():
    client = TelegramEmbedClient(
        get=lambda *_args, **_kwargs: FakeResponse(
            fixture("telegram_embed_unavailable.html")
        ),
        min_interval_seconds=0,
    )

    result = client.fetch("https://t.me/example_channel/123")

    assert result.status == "unavailable"
    assert result.reason == "message_text_not_found"
    assert not result.text


def test_http_404_is_reported_as_unavailable():
    client = TelegramEmbedClient(
        get=lambda *_args, **_kwargs: FakeResponse("", status_code=404),
        min_interval_seconds=0,
    )

    result = client.fetch("https://t.me/example_channel/123")

    assert result.status == "unavailable"
    assert result.reason == "http_404"


def test_unsupported_url_never_performs_http_request():
    calls = 0

    def get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("HTTP must not be called")

    client = TelegramEmbedClient(get=get)
    result = client.fetch("https://t.me/c/123456/789")

    assert result.status == "unsupported_url"
    assert calls == 0


def test_public_link_detection_skips_other_links():
    links = "https://example.test/a\nhttps://t.me/valid_channel/42\n"

    assert find_public_telegram_url(links) == "https://t.me/valid_channel/42"
    assert build_embed_url("https://t.me/s/valid_channel/42") == (
        "https://t.me/valid_channel/42?embed=1&mode=tme"
    )


def test_rate_limit_waits_between_requests():
    moments = iter((10.0, 10.2, 11.0))
    sleeps = []
    client = TelegramEmbedClient(
        get=lambda *_args, **_kwargs: FakeResponse(
            fixture("telegram_embed_success.html")
        ),
        min_interval_seconds=1.0,
        sleep=sleeps.append,
        monotonic=lambda: next(moments),
    )

    client.fetch("https://t.me/example_channel/1")
    client.fetch("https://t.me/example_channel/2")

    assert sleeps == [pytest.approx(0.8)]

