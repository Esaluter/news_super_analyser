from __future__ import annotations

from news_super_analyser import telegram_notify_cli


def configure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "offline-placeholder")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
    monkeypatch.setenv("GOOGLE_SHEET_URL", "https://example.test/sheet?tab=1&view=all")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "20")


def test_default_check_validates_and_formats_without_network(monkeypatch, capsys):
    configure(monkeypatch)

    class ForbiddenNotifier:
        def __init__(self, **kwargs):
            raise AssertionError("default check must not construct a network notifier")

    monkeypatch.setattr(telegram_notify_cli, "TelegramNotifier", ForbiddenNotifier)
    assert telegram_notify_cli.main([]) == 0
    output = capsys.readouterr().out
    assert "Сетевой запрос не выполнялся" in output
    assert "offline-placeholder" not in output
    assert "-1001234567890" not in output


def test_send_flag_allows_exactly_one_mocked_message(monkeypatch):
    configure(monkeypatch)
    calls = []

    class FakeNotifier:
        def __init__(self, **kwargs):
            pass

        def send(self, report):
            calls.append(report)

    monkeypatch.setattr(telegram_notify_cli, "TelegramNotifier", FakeNotifier)
    assert telegram_notify_cli.main(["--send"]) == 0
    assert len(calls) == 1
