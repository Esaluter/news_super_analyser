"""Local-only capture of raw NSPD responses for diagnostic runs."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_DEBUG_RESPONSE_BYTES = 5_000_000
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "cookie",
    "session",
)
_SENSITIVE_QUERY_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "token",
}
_TYPE_KEYS = {"type", "object_type", "category", "category_name", "categoryname"}
_IDENTIFIER_KEYS = {
    "id",
    "object_id",
    "guid",
    "cad_number",
    "cad_num",
    "cadastral_number",
    "external_id",
}


class NspdDebugRecorder:
    """Write one isolated diagnostic directory without affecting processing."""

    max_response_bytes = MAX_DEBUG_RESPONSE_BYTES

    def __init__(
        self,
        base_directory: Path,
        *,
        mode: str,
        logger: logging.Logger | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.mode = mode
        self._now = now or (lambda: datetime.now().astimezone())
        self.started_at = self._now()
        self.run_directory: Path | None = None
        self._request_count = 0
        self._current_object_id = ""
        self._requests: list[dict[str, Any]] = []
        self._objects: list[dict[str, Any]] = []
        self._diagnostic_errors: list[str] = []
        try:
            self.run_directory = _unique_run_directory(
                Path(base_directory), self.started_at
            )
        except OSError as exc:
            self._remember_file_error("create_directory", exc)

    def begin_task(self, task: dict[str, str]) -> None:
        self._current_object_id = str(task.get("object_id", ""))

    def capture_http(
        self,
        *,
        query: str,
        url: str,
        method: str,
        status: int | None,
        duration_ms: int,
        body: bytes | None,
        body_truncated: bool,
        error: str | None,
    ) -> None:
        """Capture bytes before the production NSPD parser transforms them."""

        self._request_count += 1
        sequence = self._request_count
        stem = f"request_{sequence:03d}"
        response: dict[str, Any] = {
            "body_present": body is not None,
            "body_truncated": bool(body_truncated),
            "captured_bytes": len(body or b""),
        }
        parsed: Any = None
        if body is not None:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response["is_json"] = False
                response["raw_body_file"] = f"{stem}_body.bin"
                self._safe_write_bytes(response["raw_body_file"], body)
            else:
                response["is_json"] = True
                response["body"] = _redact_sensitive(parsed)

        object_types, identifiers = _discover_fields(parsed)
        filename = f"{stem}.json"
        record = {
            "sequence": sequence,
            "requested_at": self._now().isoformat(),
            "object_id": self._current_object_id or None,
            "search_query": query,
            "parameters": {"query": query, "thematicSearchId": "1"},
            "url": _safe_url(url),
            "method": method,
            "http_status": status,
            "duration_ms": int(duration_ms),
            "response": response,
            "error": error,
        }
        self._safe_write_json(filename, record)
        self._requests.append(
            {
                "object_id": self._current_object_id,
                "file": filename,
                "query": query,
                "object_types": object_types,
                "identifiers": identifiers,
            }
        )

    def record_task(self, task: dict[str, str], evaluation: Any) -> None:
        diagnostic = evaluation.diagnostic
        attempts = []
        rejection_reasons: dict[str, int] = {}
        for attempt in diagnostic.attempts:
            for reason, count in attempt.rejection_reasons.items():
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + int(count)
            attempts.append(
                {
                    "level": attempt.level,
                    "query": attempt.query,
                    "http_requests": attempt.requests,
                    "candidates_before_filter": attempt.returned,
                    "candidates_after_filter": attempt.accepted,
                    "candidates_rejected": attempt.rejected,
                    "status": attempt.status,
                    "error_type": attempt.error_type or None,
                    "rejection_reasons": dict(attempt.rejection_reasons),
                }
            )
        requests = [
            item for item in self._requests if item["object_id"] == task["object_id"]
        ]
        numbers = list(evaluation.cadastral_numbers)
        if evaluation.cadastral_number and evaluation.cadastral_number not in numbers:
            numbers.insert(0, evaluation.cadastral_number)
        quarters = [evaluation.cadastral_quarter] if evaluation.cadastral_quarter else []
        self._objects.append(
            {
                "object_id": task["object_id"],
                "source_status": task["status"],
                "region": task["region"],
                "locality": task["locality"],
                "address": task["address"],
                "search_variants": attempts,
                "http_request_count": len(requests),
                "http_request_files": [item["file"] for item in requests],
                "candidates_before_filter": sum(
                    item["candidates_before_filter"] for item in attempts
                ),
                "candidates_after_filter": sum(
                    item["candidates_after_filter"] for item in attempts
                ),
                "cadastral_numbers": numbers,
                "cadastral_quarters": quarters,
                "available_object_types": _unique_discovered(
                    requests, "object_types"
                ),
                "available_identifiers": _unique_discovered(
                    requests, "identifiers"
                ),
                "algorithm_outcome": diagnostic.outcome,
                "final_status": evaluation.status,
                "error": evaluation.error or None,
                "rejection_reasons": rejection_reasons,
            }
        )
        self._current_object_id = ""

    def record_task_error(self, task: dict[str, str], exc: BaseException) -> None:
        self._objects.append(
            {
                "object_id": task.get("object_id", ""),
                "source_status": task.get("status", ""),
                "region": task.get("region", ""),
                "locality": task.get("locality", ""),
                "address": task.get("address", ""),
                "search_variants": [],
                "http_request_count": len(
                    [
                        item
                        for item in self._requests
                        if item["object_id"] == task.get("object_id", "")
                    ]
                ),
                "candidates_before_filter": None,
                "candidates_after_filter": None,
                "cadastral_numbers": [],
                "cadastral_quarters": [],
                "available_object_types": [],
                "available_identifiers": [],
                "final_status": "diagnostic_error",
                "error": type(exc).__name__,
            }
        )
        self._current_object_id = ""

    def record_lookup_error(self, object_id: str, reason: str) -> None:
        self._objects.append(
            {
                "object_id": object_id,
                "region": None,
                "locality": None,
                "address": None,
                "search_variants": [],
                "final_status": "lookup_error",
                "error": reason,
                "http_request_count": 0,
                "candidates_before_filter": None,
                "candidates_after_filter": None,
                "cadastral_numbers": [],
                "cadastral_quarters": [],
                "available_object_types": [],
                "available_identifiers": [],
            }
        )

    def finish(self) -> Path | None:
        finished_at = self._now()
        summary: dict[str, Any] = {
            "run_started_at": self.started_at.isoformat(),
            "run_finished_at": finished_at.isoformat(),
            "mode": self.mode,
            "object_id": None,
            "http_request_count": len(self._requests),
            "objects": self._objects,
            "diagnostic_file_errors": list(self._diagnostic_errors),
        }
        if len(self._objects) == 1:
            summary.update(self._objects[0])
        self._safe_write_json("summary.json", summary)
        return self.run_directory

    def _safe_write_json(self, filename: str, value: Any) -> None:
        if self.run_directory is None:
            return
        try:
            (self.run_directory / filename).write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            self._remember_file_error(filename, exc)

    def _safe_write_bytes(self, filename: str, value: bytes) -> None:
        if self.run_directory is None:
            return
        try:
            (self.run_directory / filename).write_bytes(value)
        except OSError as exc:
            self._remember_file_error(filename, exc)

    def _remember_file_error(self, operation: str, exc: BaseException) -> None:
        marker = f"{operation}:{type(exc).__name__}"
        self._diagnostic_errors.append(marker)
        self.logger.error("NSPD debug file error: %s", marker)


def _unique_run_directory(base: Path, started_at: datetime) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stem = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    for index in range(1000):
        suffix = "" if index == 0 else f"_{index:03d}"
        candidate = base / f"{stem}{suffix}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise OSError("cannot allocate unique NSPD debug directory")


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if key.casefold() in _SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _redact_sensitive(value: Any, key: str = "") -> Any:
    normalized = re.sub(r"[^a-z0-9_]", "", key.casefold())
    if normalized == "token" or any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact_sensitive(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _discover_fields(value: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    types: list[dict[str, str]] = []
    identifiers: list[dict[str, str]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                normalized = str(key).casefold()
                if normalized in _TYPE_KEYS and isinstance(nested, (str, int, float)):
                    types.append({"field": str(key), "value": str(nested)})
                if normalized in _IDENTIFIER_KEYS and isinstance(nested, (str, int, float)):
                    identifiers.append({"field": str(key), "value": str(nested)})
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return _unique_pairs(types), _unique_pairs(identifiers)


def _unique_pairs(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        marker = (item["field"], item["value"])
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _unique_discovered(
    requests: list[dict[str, Any]], field: str
) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for request in requests:
        values.extend(request[field])
    return _unique_pairs(values)
