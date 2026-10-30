"""HTTP adapter for the dedicated Google Apps Script web application."""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import requests

SOURCE_SHEET = "ИЖС-КОРП"
TARGET_SHEET = "НОВОСТИ"
MAX_FULL_TEXT_BATCH_SIZE = 20
MAX_GEMINI_BATCH_SIZE = 20
MAX_GIGA_BATCH_SIZE = 20
MAX_AI_BATCH_SIZE = 20
MAX_OBJECT_BATCH_SIZE = 500
MAX_CADASTRAL_TASK_BATCH_SIZE = 500


class SheetsGateway(Protocol):
    def read_source_rows(self) -> list[list[Any]]: ...

    def read_target_rows(self) -> list[list[Any]]: ...

    def append_target_row(self, row: list[str]) -> None: ...


class AppsScriptError(RuntimeError):
    """A sanitized Apps Script failure safe to write to local logs."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


T = TypeVar("T")


def retry_after_seconds(exc: BaseException) -> float | None:
    """Return a numeric Retry-After value from supported exceptions."""
    direct = getattr(exc, "retry_after", None)
    if direct is not None:
        try:
            return max(0.0, float(direct))
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None) or getattr(exc, "resp", None)
    if response is None:
        return None
    headers = getattr(response, "headers", response)
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except (AttributeError, TypeError):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def call_with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> T:
    """Retry transient HTTP operations a bounded number of times."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if getattr(exc, "retryable", True) is False or attempt == attempts:
                raise
            retry_after = retry_after_seconds(exc)
            delay = retry_after if retry_after is not None else base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, min(0.25, delay / 10)) if delay else 0
            if logger:
                logger.warning(
                    "HTTP-операция не удалась (попытка %d/%d): %s; повтор через %.2f с",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
            sleep(delay)
    raise AssertionError("unreachable")


class AppsScriptGateway:
    """Transport adapter implementing the synchronization gateway protocol."""

    def __init__(
        self,
        url: str,
        secret: str,
        *,
        timeout_seconds: float = 20.0,
        logger: logging.Logger | None = None,
        attempts: int = 3,
        post: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self._secret = secret
        self.timeout_seconds = timeout_seconds
        self.logger = logger or logging.getLogger(__name__)
        self.attempts = attempts
        self._post = post or requests.post
        self._sleep = sleep

    def _request(
        self,
        action: str,
        *,
        row: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "version": 1,
            "action": action,
            "secret": self._secret,
            "request_id": str(uuid.uuid4()),
        }
        if row is not None:
            body["row"] = row
        if parameters:
            reserved = {"version", "action", "secret", "request_id", "row"}
            if reserved.intersection(parameters):
                raise ValueError("Apps Script parameters contain a reserved field")
            body.update(parameters)
        try:
            response = self._post(
                self.url,
                json=body,
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise AppsScriptError(
                f"Apps Script transport failure: {type(exc).__name__}",
                retryable=True,
            ) from exc

        retry_after = _response_retry_after(response)
        status = int(getattr(response, "status_code", 0))
        if status == 429 or status >= 500:
            raise AppsScriptError(
                f"Apps Script HTTP failure: {status}",
                retryable=True,
                retry_after=retry_after,
            )
        if status < 200 or status >= 300:
            raise AppsScriptError(f"Apps Script HTTP failure: {status}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise AppsScriptError(
                "Apps Script returned invalid JSON", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise AppsScriptError("Apps Script returned invalid response", retryable=True)
        if not payload.get("ok"):
            reason = str(payload.get("error", "invalid_response"))[:200]
            raise AppsScriptError(
                f"Apps Script rejected request: {reason}",
                retryable=reason in {"busy", "temporary_error", "internal_error"},
                retry_after=retry_after,
            )
        return payload

    def _read(
        self, action: str, *, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return call_with_retry(
            lambda: self._request(action, parameters=parameters),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )

    def read_source_rows(self) -> list[list[Any]]:
        payload = self._read("read_source")
        values = payload.get("values")
        if not _is_rows(values):
            raise AppsScriptError("Apps Script response has invalid source values")
        return values

    def read_target_rows(self) -> list[list[Any]]:
        payload = self._read("read_target_keys")
        headers = payload.get("headers")
        keys = payload.get("keys")
        if not isinstance(headers, list) or not all(isinstance(x, str) for x in headers):
            raise AppsScriptError("Apps Script response has invalid target headers")
        if not isinstance(keys, list) or not all(isinstance(x, str) for x in keys):
            raise AppsScriptError("Apps Script response has invalid target keys")
        return [headers] + [[key] for key in keys]

    def append_target_row(self, row: list[str]) -> None:
        payload = self._request("append_target", row=row)
        status = payload.get("status")
        if status not in {"inserted", "exists"}:
            raise AppsScriptError("Apps Script response has invalid append status")

    def read_full_text_candidates(self, limit: int) -> list[dict[str, str]]:
        if not 1 <= limit <= MAX_FULL_TEXT_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_FULL_TEXT_BATCH_SIZE}"
            )
        payload = self._read(
            "read_full_text_candidates", parameters={"limit": limit}
        )
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError("Apps Script response has invalid candidates")
        normalized: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise AppsScriptError("Apps Script response has invalid candidate")
            key = candidate.get("record_key")
            links = candidate.get("links")
            source_text = candidate.get("source_text")
            if not all(
                isinstance(value, str) for value in (key, links, source_text)
            ):
                raise AppsScriptError("Apps Script response has invalid candidate fields")
            normalized.append(
                {"record_key": key, "links": links, "source_text": source_text}
            )
        return normalized

    def update_full_text(
        self,
        *,
        record_key: str,
        full_text: str,
        load_status: str,
        loaded_at: str,
    ) -> str:
        parameters = {
            "record_key": record_key,
            "full_text": full_text,
            "load_status": load_status,
            "loaded_at": loaded_at,
        }
        # The server operation is conditional and idempotent: after a lost
        # response a retry returns already_processed instead of overwriting M:O.
        payload = call_with_retry(
            lambda: self._request("update_full_text", parameters=parameters),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {"updated", "already_processed", "not_found", "error"}:
            raise AppsScriptError("Apps Script response has invalid update status")
        return str(status)

    def read_full_text_backfill_candidates(
        self, limit: int
    ) -> list[dict[str, str]]:
        if not 1 <= limit <= MAX_FULL_TEXT_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_FULL_TEXT_BATCH_SIZE}"
            )
        payload = self._read(
            "read_full_text_backfill_candidates", parameters={"limit": limit}
        )
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError("Apps Script response has invalid backfill candidates")
        result: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(
                candidate.get("record_key"), str
            ):
                raise AppsScriptError(
                    "Apps Script response has invalid backfill candidate"
                )
            result.append({"record_key": candidate["record_key"]})
        return result

    def backfill_full_text(self, *, record_key: str) -> str:
        payload = call_with_retry(
            lambda: self._request(
                "backfill_full_text", parameters={"record_key": record_key}
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {
            "updated",
            "already_processed",
            "not_found",
            "no_source_text",
        }:
            raise AppsScriptError("Apps Script response has invalid backfill status")
        return str(status)

    def read_gemini_formula_candidates(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]:
        if not 1 <= limit <= MAX_GEMINI_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_GEMINI_BATCH_SIZE}"
            )
        parameters: dict[str, Any] = {"limit": limit}
        if record_key:
            parameters["record_key"] = record_key
        payload = self._read(
            "read_gemini_formula_candidates", parameters=parameters
        )
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError("Apps Script response has invalid Gemini candidates")
        expected = {"record_key", "source_cell", "target_cell"}
        result: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not expected.issubset(candidate):
                raise AppsScriptError(
                    "Apps Script response has invalid Gemini candidate"
                )
            if not all(isinstance(candidate[field], str) for field in expected):
                raise AppsScriptError(
                    "Apps Script response has invalid Gemini candidate fields"
                )
            result.append({field: candidate[field] for field in expected})
        return result

    def prepare_gemini_formula(self, *, record_key: str) -> str:
        payload = call_with_retry(
            lambda: self._request(
                "prepare_gemini_formula", parameters={"record_key": record_key}
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {
            "updated",
            "already_processed",
            "not_found",
            "no_full_text",
        }:
            raise AppsScriptError("Apps Script response has invalid Gemini status")
        return str(status)

    def read_giga_text_candidates(
        self, limit: int, *, record_key: str | None = None
    ) -> list[dict[str, str]]:
        if not 1 <= limit <= MAX_GIGA_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_GIGA_BATCH_SIZE}")
        parameters: dict[str, Any] = {"limit": limit}
        if record_key:
            parameters["record_key"] = record_key
        payload = self._read("read_giga_text_candidates", parameters=parameters)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError("Apps Script response has invalid Giga candidates")
        expected = {"record_key", "full_text", "source_cell", "target_cell"}
        result: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not expected.issubset(candidate):
                raise AppsScriptError("Apps Script response has invalid Giga candidate")
            if not all(isinstance(candidate[field], str) for field in expected):
                raise AppsScriptError(
                    "Apps Script response has invalid Giga candidate fields"
                )
            result.append({field: candidate[field] for field in expected})
        return result

    def write_giga_text_result(self, *, record_key: str, result: str) -> str:
        payload = call_with_retry(
            lambda: self._request(
                "write_giga_text_result",
                parameters={"record_key": record_key, "result": result},
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {
            "updated",
            "already_processed",
            "not_found",
            "no_full_text",
        }:
            raise AppsScriptError("Apps Script response has invalid Giga status")
        return str(status)

    def read_ai_candidates(
        self,
        limit: int,
        *,
        record_key: str | None = None,
        claim: bool = False,
    ) -> list[dict[str, str]]:
        if not 1 <= limit <= MAX_AI_BATCH_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_AI_BATCH_SIZE}")
        parameters: dict[str, Any] = {
            "limit": limit,
            "claim": claim,
        }
        if record_key:
            parameters["record_key"] = record_key
        payload = self._read("read_ai_candidates", parameters=parameters)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError("Apps Script response has invalid AI candidates")
        expected = {
            "record_key",
            "news_datetime",
            "links",
            "gemini_text",
            "giga_text",
            "source_models",
            "news_status",
            "claim_token",
        }
        result: list[dict[str, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not expected.issubset(candidate):
                raise AppsScriptError("Apps Script response has invalid AI candidate")
            if not all(isinstance(candidate[field], str) for field in expected):
                raise AppsScriptError("Apps Script response has invalid AI candidate fields")
            result.append({field: candidate[field] for field in expected})
        return result

    def read_object_enrichment_candidates(
        self, limit: int, *, row_number: int | None = None
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= MAX_OBJECT_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_OBJECT_BATCH_SIZE}"
            )
        parameters: dict[str, Any] = {"limit": limit}
        if row_number is not None:
            if row_number < 2:
                raise ValueError("row_number must be at least 2")
            parameters["row_number"] = row_number
        payload = self._read(
            "read_object_enrichment_candidates", parameters=parameters
        )
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise AppsScriptError(
                "Apps Script response has invalid object enrichment candidates"
            )
        string_fields = {
            "analysis_id",
            "object_number",
            "name",
            "organization",
            "region",
            "locality",
            "address",
            "cadastral_number",
            "cadastral_quarter",
            "all_cadastral",
            "gemini",
        }
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            if (
                not isinstance(candidate, dict)
                or not string_fields.issubset(candidate)
                or not isinstance(candidate.get("row_number"), int)
                or not all(isinstance(candidate[field], str) for field in string_fields)
            ):
                raise AppsScriptError(
                    "Apps Script response has invalid object enrichment candidate"
                )
            result.append({**candidate})
        return result

    def update_object_enrichment(
        self,
        *,
        analysis_id: str,
        object_number: str,
        values: dict[str, str],
        prepare_gemini: bool,
    ) -> dict[str, Any]:
        allowed = {"cadastral_number", "cadastral_quarter", "all_cadastral"}
        if not set(values).issubset(allowed) or not all(
            isinstance(value, str) and value for value in values.values()
        ):
            raise ValueError("invalid object enrichment values")
        payload = call_with_retry(
            lambda: self._request(
                "update_object_enrichment",
                parameters={
                    "analysis_id": analysis_id,
                    "object_number": object_number,
                    "values": values,
                    "prepare_gemini": prepare_gemini,
                },
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        updated_fields = payload.get("updated_fields")
        if status not in {"updated", "already_processed", "not_found"}:
            raise AppsScriptError(
                "Apps Script response has invalid object enrichment status"
            )
        if not isinstance(updated_fields, list) or not all(
            isinstance(field, str) for field in updated_fields
        ):
            raise AppsScriptError(
                "Apps Script response has invalid object enrichment fields"
            )
        return {"status": str(status), "updated_fields": list(updated_fields)}

    def sync_cadastral_queue(self) -> dict[str, int]:
        payload = call_with_retry(
            lambda: self._request("sync_cadastral_queue"),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        fields = (
            "records_added",
            "results_received",
            "address_resets",
            "formulas_prepared",
            "objects_checked",
        )
        if not all(
            isinstance(payload.get(field), int) and payload[field] >= 0
            for field in fields
        ):
            raise AppsScriptError("Apps Script response has invalid cadastral sync counts")
        return {field: int(payload[field]) for field in fields}

    def read_cadastral_tasks(self, limit: int) -> dict[str, Any]:
        if not 1 <= limit <= MAX_CADASTRAL_TASK_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_CADASTRAL_TASK_BATCH_SIZE}"
            )
        payload = self._read(
            "read_cadastral_tasks", parameters={"limit": limit}
        )
        tasks = payload.get("tasks")
        total_records = payload.get("total_records")
        if not isinstance(tasks, list) or not isinstance(total_records, int):
            raise AppsScriptError("Apps Script response has invalid cadastral tasks")
        normalized: list[dict[str, str]] = []
        for task in tasks:
            normalized.append(_normalized_cadastral_task(task))
        return {"tasks": normalized, "total_records": total_records}

    def read_cadastral_task(self, object_id: str) -> dict[str, str] | None:
        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError("object_id must be a non-empty string")
        payload = self._read(
            "read_cadastral_task_by_id", parameters={"object_id": object_id}
        )
        status = payload.get("status")
        if status == "not_found":
            return None
        if status != "found":
            raise AppsScriptError(
                "Apps Script response has invalid cadastral lookup status"
            )
        task = _normalized_cadastral_task(payload.get("task"))
        if task["object_id"] != object_id:
            raise AppsScriptError("Apps Script response has mismatched cadastral task")
        return task

    def update_cadastral_task(
        self,
        *,
        object_id: str,
        region: str,
        locality: str,
        address: str,
        expected_status: str,
        cadastral_number: str,
        cadastral_quarter: str,
        all_cadastral: str,
        status: str,
        error: str,
        checked_at: str,
    ) -> str:
        values = {
            "object_id": object_id,
            "region": region,
            "locality": locality,
            "address": address,
            "expected_status": expected_status,
            "cadastral_number": cadastral_number,
            "cadastral_quarter": cadastral_quarter,
            "all_cadastral": all_cadastral,
            "status": status,
            "error": error,
            "checked_at": checked_at,
        }
        if not all(isinstance(value, str) for value in values.values()):
            raise ValueError("cadastral task values must be strings")
        payload = call_with_retry(
            lambda: self._request(
                "update_cadastral_result", parameters=values
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        result = payload.get("status")
        if result not in {"updated", "already_processed", "stale", "not_found"}:
            raise AppsScriptError("Apps Script response has invalid cadastral update status")
        return str(result)

    def finalize_ai_candidate(
        self, *, record_key: str, claim_token: str, final_status: str
    ) -> str:
        payload = call_with_retry(
            lambda: self._request(
                "finalize_ai_candidate",
                parameters={
                    "record_key": record_key,
                    "claim_token": claim_token,
                    "final_status": final_status,
                },
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {"updated", "already_finalized", "not_found"}:
            raise AppsScriptError("Apps Script response has invalid AI finalization status")
        return str(status)

    def save_ai_response(self, response_row: list[str]) -> str:
        if len(response_row) != 6 or not all(isinstance(value, str) for value in response_row):
            raise ValueError("response_row must contain 6 strings")
        payload = call_with_retry(
            lambda: self._request(
                "save_ai_response", parameters={"response_row": response_row}
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {"inserted", "exists"}:
            raise AppsScriptError("Apps Script response has invalid AI response status")
        return str(status)

    def update_ai_response_error(
        self, *, analysis_id: str, expected_error: str, error: str
    ) -> str:
        payload = call_with_retry(
            lambda: self._request(
                "update_ai_response_error",
                parameters={
                    "analysis_id": analysis_id,
                    "expected_error": expected_error,
                    "error": error,
                },
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {"updated", "exists", "not_found"}:
            raise AppsScriptError("Apps Script response has invalid AI error status")
        return str(status)

    def save_ai_analysis(
        self, analysis_row: list[str], object_rows: list[list[str]]
    ) -> dict[str, Any]:
        if len(analysis_row) != 13 or not all(
            isinstance(value, str) for value in analysis_row
        ):
            raise ValueError("analysis_row must contain 13 strings")
        if not all(
            len(row) == 14 and all(isinstance(value, str) for value in row)
            for row in object_rows
        ):
            raise ValueError("every object row must contain 14 strings")
        payload = call_with_retry(
            lambda: self._request(
                "save_ai_analysis",
                parameters={
                    "analysis_row": analysis_row,
                    "object_rows": object_rows,
                },
            ),
            attempts=self.attempts,
            sleep=self._sleep,
            logger=self.logger,
        )
        status = payload.get("status")
        if status not in {"completed", "exists"}:
            raise AppsScriptError("Apps Script response has invalid AI analysis status")
        for field in ("analysis_inserted", "objects_inserted"):
            if not isinstance(payload.get(field), int):
                raise AppsScriptError("Apps Script response has invalid AI write counts")
        return {
            "status": str(status),
            "analysis_inserted": payload["analysis_inserted"],
            "objects_inserted": payload["objects_inserted"],
        }


def _response_retry_after(response: Any) -> float | None:
    headers = getattr(response, "headers", {})
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalized_cadastral_task(value: Any) -> dict[str, str]:
    fields = (
        "object_id",
        "region",
        "locality",
        "address",
        "cadastral_number",
        "cadastral_quarter",
        "all_cadastral",
        "status",
        "error",
        "checked_at",
    )
    if (
        not isinstance(value, dict)
        or not all(field in value for field in fields)
        or not all(isinstance(value[field], str) for field in fields)
    ):
        raise AppsScriptError("Apps Script response has invalid cadastral task")
    return {field: value[field] for field in fields}


def _is_rows(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(row, list) for row in value)

