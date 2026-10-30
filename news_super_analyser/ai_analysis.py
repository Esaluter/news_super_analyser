"""Orchestration for the manual GigaChat analysis stage."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .ai_prompt import SYSTEM_PROMPT, build_user_prompt
from .sheets import call_with_retry
from .telegram_full_text import find_public_telegram_url

MODEL_NAME = "GigaChat"
MAX_SHEET_CELL_LENGTH = 50_000
LOCAL_RESPONSE_DIR = Path("data") / "ai_responses"
OBJECT_FIELDS = (
    "name",
    "type",
    "organization",
    "region",
    "locality",
    "address",
    "cadastral_number",
    "damage",
    "evidence",
    "notes",
)


class AnalysisParseError(ValueError):
    pass


class _NonRetryableModelError(RuntimeError):
    retryable = False

    def __init__(self, original: Exception) -> None:
        super().__init__(_safe_error(original))
        self.raw_response = str(getattr(original, "raw_response", "") or "")


class AnalysisGateway(Protocol):
    def read_ai_candidates(
        self,
        limit: int,
        *,
        record_key: str | None = None,
        claim: bool = False,
    ) -> list[dict[str, str]]: ...

    def finalize_ai_candidate(
        self, *, record_key: str, claim_token: str, final_status: str
    ) -> str: ...

    def save_ai_response(self, response_row: list[str]) -> str: ...

    def update_ai_response_error(
        self, *, analysis_id: str, expected_error: str, error: str
    ) -> str: ...

    def save_ai_analysis(
        self, analysis_row: list[str], object_rows: list[list[str]]
    ) -> dict[str, Any]: ...


class CompletionClient(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


@dataclass(frozen=True)
class ItemResult:
    record_key: str
    analysis_id: str
    status: str
    objects: int = 0
    error: str = ""
    text_source: str = ""


def parse_analysis_json(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.I)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise AnalysisParseError("GigaChat response is not valid JSON") from exc
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise AnalysisParseError("GigaChat response is not valid JSON") from nested_exc
    if not isinstance(value, dict):
        raise AnalysisParseError("GigaChat response root must be an object")
    objects = value.get("objects")
    if not isinstance(objects, list):
        raise AnalysisParseError("GigaChat response field objects must be an array")
    normalized_objects: list[dict[str, str | None]] = []
    for item in objects:
        if not isinstance(item, dict):
            raise AnalysisParseError("Every GigaChat object must be a JSON object")
        normalized_objects.append(
            {field: _nullable_text(item.get(field)) for field in OBJECT_FIELDS}
        )
    return {
        "incident_type": _nullable_text(value.get("incident_type")),
        "region": _nullable_text(value.get("region")),
        "locality": _nullable_text(value.get("locality")),
        "summary": _nullable_text(value.get("summary")),
        "objects": normalized_objects,
    }


class AiAnalysisProcessor:
    def __init__(
        self,
        gateway: AnalysisGateway,
        client: CompletionClient,
        *,
        output_dir: Path = LOCAL_RESPONSE_DIR,
        now: Callable[[], datetime] | None = None,
        model_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.gateway = gateway
        self.client = client
        self.output_dir = output_dir
        self._now = now or (lambda: datetime.now(timezone(timedelta(hours=5))))
        self.model_attempts = model_attempts
        self._sleep = sleep
        self.logger = logger or logging.getLogger(__name__)

    def select(
        self,
        *,
        limit: int = 1,
        record_key: str | None = None,
        claim: bool = False,
    ) -> list[dict[str, str]]:
        return self.gateway.read_ai_candidates(
            limit, record_key=record_key, claim=claim
        )

    def run(
        self, *, limit: int = 1, record_key: str | None = None
    ) -> list[ItemResult]:
        results: list[ItemResult] = []
        for candidate in self.select(limit=limit, record_key=record_key, claim=True):
            result = self.process(candidate)
            results.append(result)
            if result.status != "success":
                self.logger.warning(
                    "Финальный анализ не выполнен; record_key=%s analysis_id=%s "
                    "причина=%s",
                    _safe_identifier(result.record_key),
                    _safe_identifier(result.analysis_id),
                    _safe_log_diagnostic(result.error),
                )
        return results

    def process(self, candidate: dict[str, str]) -> ItemResult:
        try:
            result = self._process_candidate(candidate)
        except Exception as exc:
            result = ItemResult(
                candidate.get("record_key", ""), "", "error", error=_safe_error(exc)
            )
        claim_token = candidate.get("claim_token", "")
        if not claim_token:
            return result
        final_status = "Обработано" if result.status == "success" else "Ошибка анализа"
        try:
            self.gateway.finalize_ai_candidate(
                record_key=candidate["record_key"],
                claim_token=claim_token,
                final_status=final_status,
            )
        except Exception as exc:
            return ItemResult(
                result.record_key,
                result.analysis_id,
                "error",
                objects=result.objects,
                error=f"{result.error}; ошибка фиксации статуса: {_safe_error(exc)}".strip("; "),
                text_source=result.text_source,
            )
        return result

    def _process_candidate(self, candidate: dict[str, str]) -> ItemResult:
        record_key = candidate["record_key"]
        analysis_id = str(uuid.uuid4())
        requested_at = self._formatted_now()
        model_text, text_source, source_error = self._resolve_text(candidate)
        raw = ""
        error = source_error

        if not model_text:
            error = source_error or "Пригодный текст новости отсутствует"
        elif len(model_text) > MAX_SHEET_CELL_LENGTH:
            error = "Входной текст превышает лимит Google Sheets; запрос не выполнен"
        else:
            try:
                raw = call_with_retry(
                    lambda: self._complete_once(model_text),
                    attempts=self.model_attempts,
                    sleep=self._sleep,
                    logger=self.logger,
                )
            except Exception as exc:
                raw = str(getattr(exc, "raw_response", "") or "")
                error = _safe_error(exc)

        input_for_sheet = model_text
        raw_for_sheet = raw
        overflow_note = ""
        if len(model_text) > MAX_SHEET_CELL_LENGTH or len(raw) > MAX_SHEET_CELL_LENGTH:
            path = self._save_local(
                analysis_id=analysis_id,
                record_key=record_key,
                input_text=model_text,
                raw_response=raw,
                error=error,
            )
            overflow_note = f"Полные данные сохранены локально: {path.as_posix()}"
            if len(model_text) > MAX_SHEET_CELL_LENGTH:
                input_for_sheet = ""
            if len(raw) > MAX_SHEET_CELL_LENGTH:
                raw_for_sheet = ""
        response_error = "; ".join(part for part in (error, overflow_note) if part)
        response_row = [
            analysis_id,
            MODEL_NAME,
            requested_at,
            input_for_sheet,
            raw_for_sheet,
            response_error,
        ]
        try:
            self.gateway.save_ai_response(response_row)
        except Exception as exc:
            path = self._save_local(
                analysis_id=analysis_id,
                record_key=record_key,
                input_text=model_text,
                raw_response=raw,
                error=_safe_error(exc),
            )
            return ItemResult(
                record_key,
                analysis_id,
                "error",
                error=f"Ответ сохранён локально: {path}",
                text_source=text_source,
            )

        parsed: dict[str, Any] | None = None
        if raw and not error:
            try:
                parsed = parse_analysis_json(raw)
            except Exception as exc:
                error = _safe_error(exc)
                updated_error = "; ".join(
                    part for part in (error, overflow_note) if part
                )
                try:
                    self.gateway.update_ai_response_error(
                        analysis_id=analysis_id,
                        expected_error=response_error,
                        error=updated_error,
                    )
                except Exception as update_exc:
                    error = f"{error}; ошибка записи диагностики: {_safe_error(update_exc)}"

        objects = parsed["objects"] if parsed else []
        analysis_status = "Успешно" if parsed is not None else "Ошибка"
        analysis_row = [
            analysis_id,
            record_key,
            candidate.get("news_datetime", ""),
            _news_link(candidate.get("links", "")),
            MODEL_NAME,
            analysis_status,
            _cell(parsed.get("incident_type") if parsed else None),
            _cell(parsed.get("region") if parsed else None),
            _cell(parsed.get("locality") if parsed else None),
            _cell(parsed.get("summary") if parsed else None),
            str(len(objects)),
            "",
            self._formatted_now(),
        ]
        object_rows = [
            [
                analysis_id,
                str(index),
                text_source,
                *[_cell(item[field]) for field in OBJECT_FIELDS],
                candidate.get("news_datetime", ""),
            ]
            for index, item in enumerate(objects, start=1)
        ]
        try:
            self.gateway.save_ai_analysis(analysis_row, object_rows)
        except Exception as exc:
            return ItemResult(
                record_key,
                analysis_id,
                "error",
                error=f"Ошибка сохранения результата анализа: {_safe_error(exc)}",
                text_source=text_source,
            )
        return ItemResult(
            record_key,
            analysis_id,
            "success" if parsed is not None else "error",
            objects=len(objects),
            error=error,
            text_source=text_source,
        )

    def _complete_once(self, model_text: str) -> str:
        try:
            return self.client.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(model_text),
            )
        except Exception as exc:
            if hasattr(exc, "retryable"):
                raise
            raise _NonRetryableModelError(exc) from exc

    def _resolve_text(self, candidate: dict[str, str]) -> tuple[str, str, str]:
        gemini_text = candidate.get("gemini_text", "")
        giga_text = candidate.get("giga_text", "")
        parts: list[str] = []
        sources: list[str] = []
        if _usable_text(gemini_text):
            parts.append("[Gemini]\n" + gemini_text)
            sources.append("Gemini")
        if _usable_text(giga_text):
            parts.append("[GigaChat]\n" + giga_text)
            sources.append("GigaChat")
        if not parts:
            return "", "", "Готовые ответы Gemini/GigaChat отсутствуют"
        source = " + ".join(sources)
        return "\n\n".join(parts), source, ""

    def _formatted_now(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _save_local(
        self,
        *,
        analysis_id: str,
        record_key: str,
        input_text: str,
        raw_response: str,
        error: str,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{analysis_id}.json"
        path.write_text(
            json.dumps(
                {
                    "analysis_id": analysis_id,
                    "record_key": record_key,
                    "input_text": input_text,
                    "raw_response": raw_response,
                    "error": error,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return text or None
    return None


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _news_link(links: str) -> str:
    telegram = find_public_telegram_url(links)
    if telegram:
        return telegram
    match = re.search(r"https?://[^\s,;]+", links)
    return match.group(0).rstrip(".)]") if match else ""


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message[:500]


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z:._-]", "", str(value or ""))
    return cleaned[:100] or "-"


def _safe_log_diagnostic(value: str) -> str:
    text = " ".join(str(value or "unknown").split())
    text = re.sub(r"https?://\S+", "[URL]", text, flags=re.I)
    text = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=:-]+",
        "[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(token|key|secret|password|authorization)=\S+",
        r"\1=[REDACTED]",
        text,
    )
    return text[:300]


def _usable_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    service_values = {
        "ошибка",
        "недоступно",
        "неподдерживаемая ссылка",
        "загружено",
        "ожидает анализа",
        "в обработке",
        "обработано",
        "ошибка анализа",
        "пропущено",
        "сгенерировать и заполнить",
    }
    return not text.startswith("=") and text.casefold() not in service_values
