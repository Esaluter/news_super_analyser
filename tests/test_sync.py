from __future__ import annotations

import logging

import pytest

from news_super_analyser.sheets import call_with_retry
from news_super_analyser.sync import (
    GOOGLE_SHEETS_CELL_LIMIT,
    PENDING_STATUS,
    SOURCE_HEADERS,
    TARGET_HEADERS,
    Synchronizer,
)


def source_row(key: str, text: str = "Новость") -> list[str]:
    return [
        key,
        "2026-10-26T11:27:20.456612+00:00",
        "Группа",
        "Автор",
        "",
        text,
        "https://example.test/post",
        "",
        "42",
        "-100123",
        "2026-10-26T12:00:00+00:00",
    ]


class FakeGateway:
    def __init__(self, source: list[list[str]], target: list[list[str]] | None = None):
        self.source = [list(row) for row in source]
        self.target = [list(row) for row in (target or [list(TARGET_HEADERS)])]
        self.append_calls = 0
        self.fail_before_append = 0
        self.fail_after_append_once = False
        self.fail_target_reads = 0

    def read_source_rows(self) -> list[list[str]]:
        return [list(row) for row in self.source]

    def read_target_rows(self) -> list[list[str]]:
        if self.fail_target_reads:
            self.fail_target_reads -= 1
            raise OSError("target read unavailable")
        return [list(row) for row in self.target]

    def append_target_row(self, row: list[str]) -> None:
        self.append_calls += 1
        if self.fail_before_append:
            self.fail_before_append -= 1
            raise OSError("temporary network failure")
        self.target.append(list(row))
        if self.fail_after_append_once:
            self.fail_after_append_once = False
            raise TimeoutError("response was lost")


def synchronizer(gateway: FakeGateway) -> Synchronizer:
    return Synchronizer(gateway, sleep=lambda _: None)


def test_first_transfer_of_several_news():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1"), source_row("1:2")])

    result = synchronizer(gateway).run()

    assert result.new_rows == 2
    assert [row[0] for row in gateway.target[1:]] == ["1:1", "1:2"]
    assert all(row[-1] == PENDING_STATUS for row in gateway.target[1:])


def test_second_run_does_not_create_duplicates():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])
    sync = synchronizer(gateway)
    sync.run()

    result = sync.run()

    assert result.new_rows == 0
    assert result.duplicates == 1
    assert len(gateway.target) == 2


def test_new_publication_is_added_in_source_order():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])
    sync = synchronizer(gateway)
    sync.run()
    gateway.source.extend([source_row("1:2"), source_row("1:3")])

    result = sync.run()

    assert result.new_rows == 2
    assert [row[0] for row in gateway.target[1:]] == ["1:1", "1:2", "1:3"]


def test_manually_edited_existing_row_is_preserved():
    existing = source_row("1:1", "Исправлено вручную") + ["Готово"]
    gateway = FakeGateway(
        [list(SOURCE_HEADERS), source_row("1:1", "Текст источника")],
        [list(TARGET_HEADERS), existing],
    )

    synchronizer(gateway).run()

    assert gateway.target[1] == existing
    assert gateway.append_calls == 0


@pytest.mark.parametrize(
    "status",
    ["Пропущено", "Обработано", "Ошибка анализа", "В обработке"],
)
def test_tg01_preserves_all_ai_queue_statuses(status):
    existing = source_row("1:1", "Сохранённый текст") + [status]
    gateway = FakeGateway(
        [list(SOURCE_HEADERS), source_row("1:1", "Новый текст источника")],
        [list(TARGET_HEADERS), existing],
    )

    synchronizer(gateway).run()

    assert gateway.target[1][11] == status
    assert gateway.target[1][5] == "Сохранённый текст"
    assert gateway.append_calls == 0


def test_dates_are_converted_from_utc_to_utc_plus_five():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])

    synchronizer(gateway).run()

    assert gateway.target[1][1] == "26.10.2026 16:27:20"
    assert gateway.target[1][10] == "26.10.2026 17:00:00"


def test_empty_fields_are_preserved_and_short_rows_are_padded():
    short_row = ["1:1", "", "Группа"]
    gateway = FakeGateway([list(SOURCE_HEADERS), short_row])

    synchronizer(gateway).run()

    assert gateway.target[1][:3] == short_row
    assert gateway.target[1][3:11] == [""] * 8
    assert len(gateway.target[1]) == 12


def test_duplicate_record_key_inside_source_is_logged_once(caplog):
    gateway = FakeGateway(
        [list(SOURCE_HEADERS), source_row("1:1", "Первый"), source_row("1:1", "Второй")]
    )

    with caplog.at_level(logging.WARNING):
        result = synchronizer(gateway).run()

    assert result.new_rows == 1
    assert result.duplicates == 1
    assert "повтор record_key=1:1" in caplog.text


def test_missing_record_key_is_skipped_and_other_rows_continue(caplog):
    gateway = FakeGateway(
        [list(SOURCE_HEADERS), source_row(""), source_row("1:2")]
    )

    with caplog.at_level(logging.ERROR):
        result = synchronizer(gateway).run()

    assert result.errors == 1
    assert result.new_rows == 1
    assert [row[0] for row in gateway.target[1:]] == ["1:2"]
    assert "отсутствует обязательный record_key" in caplog.text


def test_network_error_is_retried_and_recovers():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])
    gateway.fail_before_append = 1

    result = synchronizer(gateway).run()

    assert result.new_rows == 1
    assert gateway.append_calls == 2
    assert len(gateway.target) == 2


def test_ambiguous_write_error_is_confirmed_without_duplicate():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])
    gateway.fail_after_append_once = True

    result = synchronizer(gateway).run()

    assert result.new_rows == 1
    assert gateway.append_calls == 1
    assert [row[0] for row in gateway.target].count("1:1") == 1


def test_write_is_not_retried_when_post_error_verification_fails():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])

    original_append = gateway.append_target_row

    def fail_write_without_appending(row: list[str]) -> None:
        original_append(row)
        raise TimeoutError("response was lost")

    gateway.append_target_row = fail_write_without_appending  # type: ignore[method-assign]
    # The initial target read succeeds; verification after the ambiguous write fails.
    original_read = gateway.read_target_rows
    read_calls = 0

    def fail_verification_read() -> list[list[str]]:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            return original_read()
        raise OSError("cannot verify")

    gateway.read_target_rows = fail_verification_read  # type: ignore[method-assign]

    result = synchronizer(gateway).run()

    assert result.new_rows == 0
    assert result.errors == 1
    assert gateway.append_calls == 1
    assert [row[0] for row in gateway.target].count("1:1") == 1


def test_long_text_is_saved_without_truncation():
    long_text = "Я" * 49_999
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1", long_text)])

    result = synchronizer(gateway).run()

    assert result.new_rows == 1
    assert gateway.target[1][5] == long_text


def test_text_over_google_cell_limit_is_rejected_not_truncated(caplog):
    text = "Я" * (GOOGLE_SHEETS_CELL_LIMIT + 1)
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1", text)])

    with caplog.at_level(logging.ERROR):
        result = synchronizer(gateway).run()

    assert result.errors == 1
    assert result.new_rows == 0
    assert gateway.append_calls == 0
    assert "значение не обрезано" in caplog.text


def test_invalid_date_is_preserved_and_warning_is_logged(caplog):
    row = source_row("1:1")
    row[1] = "не дата"
    gateway = FakeGateway([list(SOURCE_HEADERS), row])

    with caplog.at_level(logging.WARNING):
        synchronizer(gateway).run()

    assert gateway.target[1][1] == "не дата"
    assert "сохранено исходное значение" in caplog.text


def test_read_operation_has_bounded_retry():
    calls = 0

    def flaky_read():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("offline")
        return "ok"

    assert call_with_retry(flaky_read, attempts=3, sleep=lambda _: None) == "ok"
    assert calls == 3


def test_header_mismatch_stops_before_any_write():
    gateway = FakeGateway([["wrong header"], source_row("1:1")])

    with pytest.raises(ValueError, match="Неожиданные заголовки"):
        synchronizer(gateway).run()

    assert gateway.append_calls == 0


def test_empty_target_is_rejected_instead_of_overwriting_header_row():
    gateway = FakeGateway([list(SOURCE_HEADERS), source_row("1:1")])
    gateway.target = []

    with pytest.raises(ValueError, match="отсутствует строка заголовков"):
        synchronizer(gateway).run()

    assert gateway.append_calls == 0

