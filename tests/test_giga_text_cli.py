from pathlib import Path

import pytest

from news_super_analyser import giga_text_cli
from news_super_analyser.giga_text import GigaTextStats
from news_super_analyser.gigachat_client import GigaChatSettings


@pytest.mark.parametrize("limit", [0, 21])
def test_cli_rejects_invalid_limit_before_external_access(limit):
    with pytest.raises(SystemExit, match="--limit должен быть от 1 до 20"):
        giga_text_cli.main(["--dry-run", "--limit", str(limit)])


def test_apply_forces_plain_text_mode_and_specific_record_key(monkeypatch, tmp_path):
    class FakeSettings:
        apps_script_url = "https://script.google.com/macros/s/test/exec"
        apps_script_secret = "secret"
        http_timeout_seconds = 20.0
        log_file = Path(tmp_path / "test.log")

    captured = {}

    class FakeProcessor:
        def __init__(self, gateway, client, *, logger):
            self.gateway = gateway

        def run(self, limit, *, record_key=None):
            assert (limit, record_key) == (1, "-1:42")
            return GigaTextStats(candidates=1, updated=1)

    def fake_client(settings):
        captured["settings"] = settings
        return object()

    monkeypatch.setattr(giga_text_cli.Settings, "from_env", lambda: FakeSettings())
    monkeypatch.setattr(giga_text_cli, "configure_logging", lambda _: object())
    monkeypatch.setattr(
        giga_text_cli, "AppsScriptGateway", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        giga_text_cli.GigaChatSettings,
        "from_env",
        lambda: GigaChatSettings(auth_key="key", structured_output=True),
    )
    monkeypatch.setattr(giga_text_cli, "GigaChatClient", fake_client)
    monkeypatch.setattr(giga_text_cli, "GigaTextProcessor", FakeProcessor)

    assert giga_text_cli.main(
        ["--apply", "--limit", "1", "--record-key", "-1:42"]
    ) == 0
    assert captured["settings"].structured_output is False
