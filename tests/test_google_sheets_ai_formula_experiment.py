from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "experiments"
    / "google_sheets_ai_formula_experiment.py"
)
SPEC = importlib.util.spec_from_file_location("ai_formula_experiment", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_cell_handles_missing_and_present_values():
    assert module._cell([], 0, 1) == ""
    assert module._cell([["a"]], 0, 1) == ""
    assert module._cell([["a", "b"]], 0, 1) == "b"


def test_safe_write_response_excludes_unexpected_fields():
    payload = {
        "spreadsheetId": "sheet-id",
        "updatedRange": "'Тест джемини'!A3:B3",
        "updatedCells": 2,
        "access_token": "must-not-be-saved",
    }
    assert module._safe_write_response(payload) == {
        "spreadsheetId": "sheet-id",
        "updatedRange": "'Тест джемини'!A3:B3",
        "updatedCells": 2,
    }


def test_formula_and_target_cells_are_exact():
    assert module.INPUT_RANGE == "'Тест джемини'!A3"
    assert module.FORMULA_RANGE == "'Тест джемини'!B3"
    assert module.INPUT_TEXT == "Москва, АО Газпром"
    assert module.AI_FORMULA == (
        '=AI("Find one publicly available address of the company in the referenced '
        'cell. Return only the address in Russian.";A3)'
    )
