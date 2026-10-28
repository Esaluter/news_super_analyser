from __future__ import annotations

import pytest

from news_super_analyser.ai_analysis_cli import build_parser, main


@pytest.mark.parametrize("limit", [1, 5, 20])
def test_cli_parser_accepts_supported_batch_sizes(limit):
    args = build_parser().parse_args(["--dry-run", "--limit", str(limit)])
    assert args.limit == limit


@pytest.mark.parametrize("limit", [0, 21])
def test_cli_rejects_out_of_range_batch_sizes_before_external_access(limit):
    with pytest.raises(SystemExit, match="--limit должен быть от 1 до 20"):
        main(["--dry-run", "--limit", str(limit)])
