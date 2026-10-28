"""Independent plain-text GigaChat analysis for the НОВОСТИ!Q column."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from .gigachat_client import GigaChatError
from .sheets import call_with_retry
from .text_normalization import normalize_publication_text


GIGA_TEXT_PROMPT = """Analyze the following Russian news article. Extract all physical property objects mentioned, including damaged and undamaged objects.

For each object provide:

- Object name or description
- Object type
- Organization or owner, if stated
- Region
- Locality
- Exact address, if stated
- Cadastral number, if stated
- Damage status
- Quantity, if multiple objects are mentioned
- Short exact evidence quote from the article

Also identify the incident type and provide a short incident summary.

Do not invent missing information. Use "Не указано" for unknown values. Preserve quantities and distinguish groups of objects from individually identified properties.

Return the entire result in Russian as structured plain text, not JSON or Markdown code blocks."""

SYSTEM_PROMPT = (
    "You extract factual information from Russian news. Follow the requested "
    "plain-text structure and do not add facts absent from the article."
)
MAX_SHEET_CELL_LENGTH = 50_000


class GigaTextGateway(Protocol):
    def read_giga_text_candidates(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]: ...

    def write_giga_text_result(self, *, record_key: str, result: str) -> str: ...


class CompletionClient(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


@dataclass(frozen=True)
class GigaTextStats:
    candidates: int = 0
    model_calls: int = 0
    updated: int = 0
    already_processed: int = 0
    no_full_text: int = 0
    not_found: int = 0
    model_errors: int = 0
    write_errors: int = 0


def build_giga_text_user_prompt(text: str) -> str:
    return f"{GIGA_TEXT_PROMPT}\n\nArticle:\n{text}"


def validate_plain_text_response(response: str) -> str:
    if not isinstance(response, str) or not response.strip():
        raise ValueError("GigaChat returned an empty plain-text response")
    stripped = response.strip()
    if stripped.startswith("```") or stripped.endswith("```"):
        raise ValueError("GigaChat returned a Markdown code block")
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        raise ValueError("GigaChat returned JSON instead of structured plain text")
    if len(response) > MAX_SHEET_CELL_LENGTH:
        raise ValueError("GigaChat response exceeds the Google Sheets cell limit")
    return response


class GigaTextProcessor:
    def __init__(
        self,
        gateway: GigaTextGateway,
        client: CompletionClient,
        *,
        model_attempts: int = 3,
        sleep=None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.client = client
        self.model_attempts = model_attempts
        self._sleep = sleep
        self.logger = logger or logging.getLogger(__name__)

    def select(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]:
        return self.gateway.read_giga_text_candidates(limit, record_key=record_key)

    def run(self, limit: int, *, record_key: str | None = None) -> GigaTextStats:
        candidates = self.select(limit, record_key=record_key)
        model_calls = updated = already = no_text = not_found = 0
        model_errors = write_errors = 0
        for candidate in candidates:
            key = candidate.get("record_key", "").strip()
            model_text = normalize_publication_text(candidate.get("full_text", ""))
            if not key or not model_text:
                no_text += 1
                continue
            try:
                model_calls += 1
                kwargs = {
                    "attempts": self.model_attempts,
                    "logger": self.logger,
                }
                if self._sleep is not None:
                    kwargs["sleep"] = self._sleep
                response = call_with_retry(
                    lambda: self.client.complete(
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=build_giga_text_user_prompt(model_text),
                    ),
                    **kwargs,
                )
                response = validate_plain_text_response(response)
            except Exception as exc:
                model_errors += 1
                self.logger.warning(
                    "GigaChat plain-text анализ не выполнен для record_key=%s: %s",
                    key,
                    _safe_diagnostic(exc),
                )
                continue

            try:
                status = self.gateway.write_giga_text_result(
                    record_key=key, result=response
                )
            except Exception as exc:
                write_errors += 1
                self.logger.warning(
                    "Ответ GigaChat не записан для record_key=%s: %s",
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
                write_errors += 1

        return GigaTextStats(
            candidates=len(candidates),
            model_calls=model_calls,
            updated=updated,
            already_processed=already,
            no_full_text=no_text,
            not_found=not_found,
            model_errors=model_errors,
            write_errors=write_errors,
        )


def _safe_diagnostic(exc: Exception) -> str:
    if isinstance(exc, GigaChatError):
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)[:300]
    return type(exc).__name__
