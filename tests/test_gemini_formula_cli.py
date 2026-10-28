from pathlib import Path

import pytest

from news_super_analyser import gemini_formula_cli
from news_super_analyser.gemini_formula import GeminiFormulaStats


@pytest.mark.parametrize("limit", [0, 21])
def test_cli_rejects_invalid_limit_before_external_access(limit):
    with pytest.raises(SystemExit, match="--limit должен быть от 1 до 20"):
        gemini_formula_cli.main(["--dry-run", "--limit", str(limit)])


def test_apply_is_manual_and_passes_specific_record_key(monkeypatch, tmp_path):
    class FakeSettings:
        apps_script_url = "https://script.google.com/macros/s/test/exec"
        apps_script_secret = "secret"
        http_timeout_seconds = 20.0
        log_file = Path(tmp_path / "test.log")

    class FakeProcessor:
        def __init__(self, gateway):
            self.gateway = gateway

        def run(self, limit, *, record_key=None):
            assert (limit, record_key) == (1, "-1:42")
            return GeminiFormulaStats(candidates=1, updated=1)

    monkeypatch.setattr(gemini_formula_cli.Settings, "from_env", lambda: FakeSettings())
    monkeypatch.setattr(
        gemini_formula_cli, "AppsScriptGateway", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(gemini_formula_cli, "GeminiFormulaProcessor", FakeProcessor)

    assert gemini_formula_cli.main(
        ["--apply", "--limit", "1", "--record-key", "-1:42"]
    ) == 0
