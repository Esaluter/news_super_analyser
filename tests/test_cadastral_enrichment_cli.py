from pathlib import Path

import pytest

from news_super_analyser import cadastral_enrichment_cli
from news_super_analyser.cadastral_enrichment import EnrichmentStats


def test_rejection_diagnostics_require_explicit_flag():
    normal = cadastral_enrichment_cli.build_parser().parse_args(["--dry-run"])
    diagnostic = cadastral_enrichment_cli.build_parser().parse_args(
        ["--dry-run", "--diagnostic-rejections"]
    )

    assert normal.diagnostic_rejections is False
    assert diagnostic.diagnostic_rejections is True


@pytest.mark.parametrize("limit", [0, 501])
def test_cli_rejects_invalid_limit_before_external_access(limit):
    with pytest.raises(SystemExit, match="--limit должен быть от 1 до 500"):
        cadastral_enrichment_cli.main(["--dry-run", "--limit", str(limit)])


def test_dry_run_never_enables_apply(monkeypatch, tmp_path):
    class FakeSettings:
        apps_script_url = "https://script.google.com/macros/s/test/exec"
        apps_script_secret = "secret"
        http_timeout_seconds = 20.0
        log_file = Path(tmp_path / "test.log")

    calls = []

    class FakeProcessor:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, limit, *, row_number=None, apply=False):
            calls.append((limit, row_number, apply))
            return EnrichmentStats(checked=1)

    monkeypatch.setattr(cadastral_enrichment_cli.Settings, "from_env", lambda: FakeSettings())
    monkeypatch.setattr(
        cadastral_enrichment_cli, "AppsScriptGateway", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        cadastral_enrichment_cli, "PynspdCadastralSearcher", lambda: object()
    )
    monkeypatch.setattr(
        cadastral_enrichment_cli, "CadastralEnrichmentProcessor", FakeProcessor
    )

    assert cadastral_enrichment_cli.main(
        ["--dry-run", "--limit", "1", "--row", "7"]
    ) == 0
    assert calls == [(1, 7, False)]
