from news_super_analyser.gemini_formula import (
    GEMINI_PROMPT,
    GeminiFormulaProcessor,
    build_gemini_formula,
)


class Gateway:
    def __init__(self, candidates, statuses=None):
        self.candidates = candidates
        self.statuses = list(statuses or [])
        self.read_calls = []
        self.prepare_calls = []

    def read_gemini_formula_candidates(self, limit, *, record_key=None):
        self.read_calls.append((limit, record_key))
        return self.candidates[:limit]

    def prepare_gemini_formula(self, *, record_key):
        self.prepare_calls.append(record_key)
        return self.statuses.pop(0)


def test_formula_uses_same_row_m_and_agreed_plain_text_prompt():
    formula = build_gemini_formula(17)

    assert formula == f'=AI("{GEMINI_PROMPT}";M17)'
    assert "structured plain-text report in Russian, not JSON" in formula
    for required in (
        "incident type",
        "brief summary",
        "affected object",
        "owner",
        "address",
        "cadastral number",
        "damage",
        "confirming quotations",
    ):
        assert required in formula


def test_processor_is_idempotent_and_does_not_overwrite_results():
    gateway = Gateway(
        [
            {"record_key": "-1:1", "source_cell": "M2", "target_cell": "P2"},
            {"record_key": "-1:2", "source_cell": "M3", "target_cell": "P3"},
        ],
        ["updated", "already_processed"],
    )

    stats = GeminiFormulaProcessor(gateway).run(2)

    assert gateway.prepare_calls == ["-1:1", "-1:2"]
    assert stats.updated == 1
    assert stats.already_processed == 1
    assert stats.errors == 0


def test_processor_passes_specific_record_key_and_counts_safe_races():
    gateway = Gateway(
        [{"record_key": "-1:9", "source_cell": "M9", "target_cell": "P9"}],
        ["no_full_text"],
    )

    stats = GeminiFormulaProcessor(gateway).run(1, record_key="-1:9")

    assert gateway.read_calls == [(1, "-1:9")]
    assert stats.no_full_text == 1
