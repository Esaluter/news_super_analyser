"""Prepare native Google Sheets =AI() formulas without running Gemini."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol


GEMINI_PROMPT = (
    "Analyze the news text in the referenced cell. Return a structured plain-text "
    "report in Russian, not JSON. Include the incident type, a brief summary, and "
    "every affected object. For each object include its name or description, object "
    "type, owner or responsible organization, exact address or location, cadastral "
    "number, damage, and short confirming quotations from the source text. Clearly "
    "mark information that is not stated. Do not invent facts or use external "
    "information."
)


class GeminiFormulaGateway(Protocol):
    def read_gemini_formula_candidates(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]: ...

    def prepare_gemini_formula(self, *, record_key: str) -> str: ...


@dataclass(frozen=True)
class GeminiFormulaStats:
    candidates: int = 0
    updated: int = 0
    already_processed: int = 0
    no_full_text: int = 0
    not_found: int = 0
    errors: int = 0


def build_gemini_formula(row_number: int) -> str:
    """Return the exact locale-specific formula used by Apps Script."""
    if row_number < 2:
        raise ValueError("row_number must be at least 2")
    escaped_prompt = GEMINI_PROMPT.replace('"', '""')
    return f'=AI("{escaped_prompt}";M{row_number})'


class GeminiFormulaProcessor:
    def __init__(
        self,
        gateway: GeminiFormulaGateway,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.logger = logger or logging.getLogger(__name__)

    def select(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]:
        return self.gateway.read_gemini_formula_candidates(
            limit, record_key=record_key
        )

    def run(
        self, limit: int, *, record_key: str | None = None
    ) -> GeminiFormulaStats:
        candidates = self.select(limit, record_key=record_key)
        updated = already = no_text = not_found = errors = 0
        for candidate in candidates:
            key = candidate.get("record_key", "").strip()
            if not key:
                errors += 1
                continue
            try:
                status = self.gateway.prepare_gemini_formula(record_key=key)
            except Exception as exc:
                errors += 1
                self.logger.warning(
                    "Не удалось подготовить формулу Gemini для record_key=%s: %s",
                    key,
                    type(exc).__name__,
                )
                continue
            if status == "updated":
                updated += 1
            elif status == "already_processed":
                already += 1
            elif status == "no_full_text":
                no_text += 1
            elif status == "not_found":
                not_found += 1
            else:
                errors += 1
        return GeminiFormulaStats(
            candidates=len(candidates),
            updated=updated,
            already_processed=already,
            no_full_text=no_text,
            not_found=not_found,
            errors=errors,
        )
