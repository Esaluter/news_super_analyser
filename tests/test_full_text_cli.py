from pathlib import Path

from news_super_analyser import full_text_cli
from news_super_analyser.full_text_processor import BackfillStats


def test_backfill_mode_never_constructs_telegram_client(monkeypatch, tmp_path):
    class FakeSettings:
        apps_script_url = "https://script.google.com/macros/s/test/exec"
        apps_script_secret = "secret"
        http_timeout_seconds = 20.0
        log_file = Path(tmp_path / "test.log")

    class FakeBackfillProcessor:
        def __init__(self, gateway, *, logger):
            self.gateway = gateway

        def run(self, limit):
            assert limit == 1
            return BackfillStats(candidates=1, updated=1)

    monkeypatch.setattr(full_text_cli.Settings, "from_env", lambda: FakeSettings())
    monkeypatch.setattr(full_text_cli, "configure_logging", lambda _: object())
    monkeypatch.setattr(full_text_cli, "AppsScriptGateway", lambda *args, **kwargs: object())
    monkeypatch.setattr(full_text_cli, "FullTextBackfillProcessor", FakeBackfillProcessor)
    monkeypatch.setattr(
        full_text_cli,
        "TelegramEmbedClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram client must not be created in backfill mode")
        ),
    )

    result = full_text_cli.main(
        ["--limit", "1", "--backfill-source-text", "--apply"]
    )

    assert result == 0
