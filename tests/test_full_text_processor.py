from __future__ import annotations

from news_super_analyser.full_text_processor import (
    FullTextBackfillProcessor,
    FullTextProcessor,
)
from news_super_analyser.telegram_full_text import TelegramPostResult


class FakeGateway:
    def __init__(self, candidates, update_status="updated", update_error=None):
        self.candidates = candidates
        self.update_status = update_status
        self.update_error = update_error
        self.updates = []
        self.backfill_calls = []
        self.backfill_statuses = []

    def read_full_text_candidates(self, limit):
        return self.candidates[:limit]

    def update_full_text(self, **kwargs):
        self.updates.append(kwargs)
        if self.update_error:
            raise self.update_error
        return self.update_status

    def read_full_text_backfill_candidates(self, limit):
        return self.candidates[:limit]

    def backfill_full_text(self, *, record_key):
        self.backfill_calls.append(record_key)
        if self.backfill_statuses:
            return self.backfill_statuses.pop(0)
        return self.update_status


class FakeTelegram:
    def __init__(self, result):
        self.result = result
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return self.result


def test_successful_candidate_updates_only_full_text_payload():
    gateway = FakeGateway(
        [{
            "record_key": "-1:42",
            "links": "https://t.me/channel_name/42",
            "source_text": "Исходный текст",
        }]
    )
    telegram = FakeTelegram(
        TelegramPostResult(
            url="https://t.me/channel_name/42", status="loaded", text="Полный\n\nтекст"
        )
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.fetched == 1
    assert stats.updated == 1
    assert gateway.updates[0]["record_key"] == "-1:42"
    assert gateway.updates[0]["full_text"] == "Полный\n\nтекст"
    assert gateway.updates[0]["load_status"] == "Загружено"
    assert set(gateway.updates[0]) == {
        "record_key",
        "full_text",
        "load_status",
        "loaded_at",
    }


def test_repeat_result_is_counted_without_overwrite_attempt_in_python():
    gateway = FakeGateway(
        [{
            "record_key": "-1:42",
            "links": "https://t.me/channel_name/42",
            "source_text": "Исходный текст",
        }],
        update_status="already_processed",
    )
    telegram = FakeTelegram(
        TelegramPostResult(
            url="https://t.me/channel_name/42", status="loaded", text="Текст"
        )
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.already_processed == 1
    assert stats.updated == 0


def test_missing_record_key_is_skipped_without_sheet_update():
    gateway = FakeGateway(
        [{
            "record_key": "",
            "links": "https://t.me/channel_name/42",
            "source_text": "Исходный текст",
        }]
    )
    telegram = FakeTelegram(
        TelegramPostResult(
            url="https://t.me/channel_name/42", status="loaded", text="Текст"
        )
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.errors == 1
    assert gateway.updates == []
    assert telegram.urls == []


def test_unsupported_link_is_recorded_without_http_fetch():
    gateway = FakeGateway(
        [{
            "record_key": "-1:42",
            "links": "https://example.test/article",
            "source_text": "Исходный текст публикации",
        }]
    )
    telegram = FakeTelegram(
        TelegramPostResult(url="", status="loaded", text="unexpected")
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.fetch_failures == 1
    assert telegram.urls == []
    assert gateway.updates[0]["load_status"] == "Неподдерживаемая ссылка"
    assert gateway.updates[0]["full_text"] == "Исходный текст публикации"
    assert gateway.updates[0]["loaded_at"] == ""


def test_apps_script_update_error_does_not_stop_other_accounting():
    gateway = FakeGateway(
        [{
            "record_key": "-1:42",
            "links": "https://t.me/channel_name/42",
            "source_text": "Исходный текст",
        }],
        update_error=RuntimeError("Apps Script failed"),
    )
    telegram = FakeTelegram(
        TelegramPostResult(
            url="https://t.me/channel_name/42", status="loaded", text="Текст"
        )
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.fetched == 1
    assert stats.errors == 1
    assert stats.updated == 0


def test_failed_fetch_uses_source_text_but_preserves_failure_status():
    gateway = FakeGateway([{
        "record_key": "-1:42",
        "links": "https://t.me/channel_name/42",
        "source_text": "Текст из F",
    }])
    telegram = FakeTelegram(
        TelegramPostResult(
            url="https://t.me/channel_name/42",
            status="unavailable",
            reason="not_found",
        )
    )

    stats = FullTextProcessor(gateway, telegram).run(1)

    assert stats.fetch_failures == 1
    assert gateway.updates[0]["full_text"] == "Текст из F"
    assert gateway.updates[0]["load_status"] == "Недоступно"
    assert gateway.updates[0]["loaded_at"] == ""


def test_failed_fetch_with_blank_source_keeps_full_text_empty():
    gateway = FakeGateway([{
        "record_key": "-1:42",
        "links": "https://t.me/channel_name/42",
        "source_text": "   ",
    }])
    telegram = FakeTelegram(
        TelegramPostResult(url="", status="error", reason="timeout")
    )

    FullTextProcessor(gateway, telegram).run(1)

    assert gateway.updates[0]["full_text"] == ""
    assert gateway.updates[0]["load_status"] == "Ошибка"


def test_backfill_has_no_network_dependency_and_is_idempotent():
    gateway = FakeGateway(
        [{"record_key": "-1:42"}, {"record_key": "-1:43"}],
    )
    gateway.backfill_statuses = ["updated", "already_processed"]

    stats = FullTextBackfillProcessor(gateway).run(2)

    assert gateway.backfill_calls == ["-1:42", "-1:43"]
    assert stats.updated == 1
    assert stats.already_processed == 1
    assert stats.errors == 0


def test_backfill_counts_missing_source_without_retry():
    gateway = FakeGateway([{"record_key": "-1:42"}])
    gateway.backfill_statuses = ["no_source_text"]

    stats = FullTextBackfillProcessor(gateway).run(1)

    assert gateway.backfill_calls == ["-1:42"]
    assert stats.no_source_text == 1

