"""TG-08.1 cadastral enrichment for rows of the ``ОБЪЕКТЫ`` sheet."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


CADASTRAL_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,7}):(\d+)$")
CADASTRAL_QUARTER_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,7})$")
MAX_OBJECT_BATCH_SIZE = 500
OBJECT_GEMINI_PROMPT = (
    "Find one publicly available street address of the organization or property "
    "described in the provided row, located in Russia. Use the organization name "
    "from column F if available; otherwise use the property name from column D. "
    "Use the city from column H and the region from column G. Return only the "
    "address, preferably in Russian. If no reliable address can be found, return "
    "'Not found'. Do not invent information."
)


class ObjectEnrichmentGateway(Protocol):
    def read_object_enrichment_candidates(
        self, limit: int, *, row_number: int | None = None
    ) -> list[dict[str, Any]]: ...

    def update_object_enrichment(
        self,
        *,
        analysis_id: str,
        object_number: str,
        values: dict[str, str],
        prepare_gemini: bool,
    ) -> dict[str, Any]: ...


class CadastralSearcher(Protocol):
    def search(
        self,
        query: str,
        *,
        region: str,
        locality: str,
        address: str,
        timeout_seconds: float | None = None,
        timeout_reason: str = "request_timeout",
    ) -> list[dict[str, str]]: ...


class NspdDeadlineExceeded(TimeoutError):
    """A hard wall-clock NSPD deadline expired."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NspdWorkerError(ConnectionError):
    """A sanitized failure returned by the isolated NSPD worker."""

    def __init__(self, error_type: str) -> None:
        self.error_type = re.sub(r"[^0-9A-Za-z_.-]", "", error_type)[:100]
        super().__init__(self.error_type or "worker_error")


@dataclass(frozen=True)
class CadastralLimits:
    http_timeout_seconds: float = 10.0
    row_timeout_seconds: float = 30.0
    stage_timeout_seconds: float = 120.0
    attempts: int = 2
    request_delay_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "CadastralLimits":
        limits = cls(
            http_timeout_seconds=_positive_float_env("NSPD_CLIENT_TIMEOUT", 10.0),
            row_timeout_seconds=_positive_float_env("NSPD_ROW_TIMEOUT_SECONDS", 30.0),
            stage_timeout_seconds=_positive_float_env(
                "NSPD_STAGE_TIMEOUT_SECONDS", 120.0
            ),
            attempts=_bounded_int_env("NSPD_ATTEMPTS", 2, minimum=1, maximum=3),
            request_delay_seconds=_nonnegative_float_env(
                "NSPD_REQUEST_DELAY_SECONDS", 1.0
            ),
        )
        if limits.http_timeout_seconds > limits.row_timeout_seconds:
            raise ValueError("NSPD_CLIENT_TIMEOUT must not exceed row timeout")
        if limits.row_timeout_seconds > limits.stage_timeout_seconds:
            raise ValueError("NSPD row timeout must not exceed stage timeout")
        return limits


@dataclass
class SearchAttemptDiagnostic:
    level: int
    query: str
    requests: int
    returned: int
    accepted: int
    rejected: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    status: str = "empty"
    error_type: str = ""
    rejected_samples: list["RejectedCandidateDiagnostic"] = field(
        default_factory=list
    )


@dataclass
class RejectedCandidateDiagnostic:
    original_address: str
    normalized_candidate: str
    normalized_source: str
    reason: str
    source_street: str
    candidate_street: str
    source_house: str
    candidate_house: str
    source_building: str
    candidate_building: str


@dataclass
class RowSearchDiagnostic:
    row_number: int | str
    attempts: list[SearchAttemptDiagnostic] = field(default_factory=list)
    outcome: str = "not_needed"
    matching_numbers: int = 0


@dataclass
class EnrichmentStats:
    checked: int = 0
    nspd_queries: int = 0
    cadastral_numbers_found: int = 0
    cells_planned: int = 0
    cells_updated: int = 0
    rows_updated: int = 0
    errors: int = 0
    deferred_rows: int = 0
    timed_out_rows: int = 0
    existing_numbers: int = 0
    successful_rows: int = 0
    row_errors: list[str] = field(default_factory=list)
    planned_rows: list[dict[str, Any]] = field(default_factory=list)
    search_diagnostics: list[RowSearchDiagnostic] = field(default_factory=list)


def normalize_cadastral_number(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "").replace("-", ":")
    match = CADASTRAL_RE.fullmatch(text)
    return ":".join(match.groups()) if match else ""


def cadastral_quarter(value: Any) -> str:
    number = normalize_cadastral_number(value)
    return ":".join(number.split(":")[:3]) if number else ""


def normalize_cadastral_quarter(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "").replace("-", ":")
    match = CADASTRAL_QUARTER_RE.fullmatch(text)
    return ":".join(match.groups()) if match else ""


def cadastral_numbers_from_display(value: Any) -> list[str]:
    """Read numbers from both legacy and descriptive one-object-per-line text."""

    result: list[str] = []
    seen: set[str] = set()
    for line in str(value or "").splitlines():
        number = normalize_cadastral_number(line.split(" — ", 1)[0])
        if number and number not in seen:
            seen.add(number)
            result.append(number)
    return result


def format_cadastral_candidate(candidate: dict[str, str]) -> str:
    number = normalize_cadastral_number(candidate.get("cadastral_number"))
    if not number:
        return ""
    category = _single_line(candidate.get("category_name"))
    detail = next(
        (
            text
            for text in (
                _single_line(candidate.get("building_name")),
                _single_line(candidate.get("purpose")),
                _single_line(candidate.get("permitted_use")),
            )
            if text
        ),
        "",
    )
    if category and detail:
        return f"{number} — {category} ({detail})"
    if category:
        return f"{number} — {category}"
    if detail:
        return f"{number} — {detail}"
    return number


def build_search_query(*, region: str, locality: str, address: str) -> str:
    return ", ".join(
        part.strip() for part in (region, locality, address) if part and part.strip()
    )


def build_object_gemini_formula(row_number: int) -> str:
    if row_number < 2:
        raise ValueError("row_number must be at least 2")
    prompt = OBJECT_GEMINI_PROMPT.replace('"', '""')
    return f'=AI("{prompt}";D{row_number}:I{row_number})'


def extract_candidates(features: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for feature in features or []:
        properties = _source_value(feature, "properties")
        options = _source_value(properties, "options") if properties else None
        if not options:
            continue
        number = normalize_cadastral_number(
            _source_value(options, "cad_number", "cad_num")
        )
        if not number or number in seen:
            continue
        address = (
            _source_value(options, "readable_address", "address_readable_address")
            or ""
        )
        seen.add(number)
        result.append(
            {
                "cadastral_number": number,
                "address": str(address).strip(),
                "category": str(_source_value(properties, "category") or ""),
                "category_name": str(
                    _source_value(properties, "category_name", "categoryName") or ""
                ),
                "building_name": str(
                    _source_value(options, "building_name") or ""
                ),
                "purpose": str(_source_value(options, "purpose") or ""),
                "permitted_use": str(
                    _source_value(
                        options, "permitted_use_established_by_document"
                    )
                    or ""
                ),
                "cadastral_quarter": normalize_cadastral_quarter(
                    _source_value(options, "quarter_cad_number")
                ),
            }
        )
    return result


def _source_value(source: Any, *names: str) -> Any:
    if isinstance(source, dict):
        for name in names:
            value = source.get(name)
            if value is not None and value != "":
                return value
        return None
    for name in names:
        value = getattr(source, name, None)
        if value is not None and value != "":
            return value
    return None


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalized_text(value: Any) -> str:
    """Normalize text; dots and commas become separators rather than joins."""
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[.,]", " ", text)
    text = re.sub(r"[^0-9a-zа-я/]+", " ", text)
    return " ".join(text.split())


def _canonical_freeform_address(value: Any) -> str:
    replacements = {
        "г": "город",
        "обл": "область",
        "ул": "улица",
        "д": "дом",
        "корп": "корпус",
        "стр": "строение",
    }
    return " ".join(
        replacements.get(token, token) for token in _normalized_text(value).split()
    )


def _region_key(value: Any) -> str:
    words = _normalized_text(value).split()
    ignored = {
        "область", "обл", "край", "республика", "респ", "автономный",
        "автономная", "округ", "ао", "город", "г",
    }
    return " ".join(word for word in words if word not in ignored)


def _locality_key(value: Any) -> str:
    words = _normalized_text(value).split()
    prefixes = {"г", "город", "с", "село", "п", "пос", "поселок", "деревня", "д"}
    while words and words[0] in prefixes:
        words.pop(0)
    return " ".join(words)


def _house(value: Any, *, allow_trailing: bool = False) -> str:
    text = _normalized_text(value)
    match = re.search(r"\b(?:д|дом)\s*(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)", text)
    if not match and allow_trailing:
        match = re.search(r"\b(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)$", text)
    return match.group(1) if match else ""


def _qualifier(value: Any, names: str) -> str:
    match = re.search(
        rf"\b(?:{names})\s*(\d+[а-яa-z]?)\b", _normalized_text(value)
    )
    return match.group(1) if match else ""


def _street(value: Any) -> str:
    marker = (
        r"(?:улица|ул|проспект|просп|пр\s+т|переулок|пер|шоссе|ш|"
        r"бульвар|бул|б\s+р|проезд|пр\s+д|набережная|наб|площадь|пл|"
        r"аллея|тупик|линия)"
    )
    administrative = re.compile(
        r"\b(?:российская федерация|муниципальный округ|городской округ|"
        r"вн\s+тер\s+г|район|город|область|республика|край)\b"
    )
    for raw_segment in re.split(r"[,;]", str(value or "")):
        segment = _normalized_text(raw_segment)
        prefix = re.search(rf"\b{marker}\s+(.+)$", segment)
        if prefix:
            candidate = re.split(
                r"\s+(?:д|дом|стр|строение|корп|корпус)\b",
                prefix.group(1),
                maxsplit=1,
            )[0].strip()
            if candidate and not administrative.search(candidate):
                return candidate
        suffix = re.search(rf"^(.+?)\s+{marker}\b", segment)
        if suffix:
            candidate = suffix.group(1).strip()
            if candidate and not administrative.search(candidate):
                return candidate
    text = _normalized_text(value)
    match = re.search(
        rf"\b{marker}\s+(.+?)(?=\s+(?:д|дом|стр|строение|корп|корпус)\b|$)",
        text,
    )
    if not match:
        return ""
    candidate = " ".join(match.group(1).split())
    return "" if administrative.search(candidate) else candidate


def normalize_address(*, region: str, locality: str, address: str) -> str:
    """Return a canonical NSPD search and comparison representation."""
    region_key = _region_key(region)
    locality_key = _locality_key(locality)
    components: list[str] = []
    for geo in (region_key, locality_key):
        if geo and geo not in components:
            components.append(geo)
    street = _street(address)
    house = _house(address, allow_trailing=True)
    corpus = _qualifier(address, "корп|корпус")
    building = _qualifier(address, "стр|строение")
    if street:
        components.append(street)
    if house:
        components.append(house)
    if corpus:
        components.extend(("корпус", corpus))
    if building:
        components.extend(("строение", building))
    return " ".join(components)


def build_search_variants(
    *, region: str, locality: str, address: str
) -> list[tuple[int, str]]:
    original = build_search_query(region=region, locality=locality, address=address)
    normalized = normalize_address(region=region, locality=locality, address=address)
    geo: list[str] = []
    for value in (_region_key(region), _locality_key(locality)):
        if value and value not in geo:
            geo.append(value)
    expanded_parts = [*geo, _street(address), _house(address, allow_trailing=True)]
    expanded = ", ".join(part for part in expanded_parts if part)
    variants: list[tuple[int, str]] = []
    seen: set[str] = set()
    for level, query in ((1, original), (2, normalized), (3, expanded)):
        key = _normalized_text(query)
        if query and key and key not in seen:
            seen.add(key)
            variants.append((level, query))
    return variants


def is_specific_address(address: str) -> bool:
    return bool(_house(address, allow_trailing=True) and _street(address))


def candidate_rejection_reason(
    candidate: dict[str, str],
    *,
    region: str,
    locality: str,
    address: str,
) -> str | None:
    candidate_address = str(candidate.get("address", "")).strip()
    if not candidate_address:
        return "недостаточно данных"
    normalized_candidate = _normalized_text(candidate_address)
    locality_key = _locality_key(locality)
    region_key = _region_key(region)
    if region_key and region_key != locality_key and region_key not in normalized_candidate:
        return "регион"
    if locality_key and locality_key not in normalized_candidate:
        return "населённый пункт"

    source_street = _street(address)
    candidate_street = _street(candidate_address)
    if not candidate_street:
        return "недостаточно данных"
    if source_street and candidate_street != source_street:
        return "улица"

    source_house = _house(address, allow_trailing=True)
    candidate_house = _house(candidate_address, allow_trailing=True)
    if not candidate_house:
        return "недостаточно данных"
    if source_house and candidate_house != source_house:
        return "дом"

    for names, reason in (("корп|корпус", "корпус"), ("стр|строение", "строение")):
        source_value = _qualifier(address, names)
        if not source_value:
            continue
        candidate_value = _qualifier(candidate_address, names)
        if not candidate_value:
            return "недостаточно данных"
        if candidate_value != source_value:
            return reason
    return None


def filter_candidates_with_reasons(
    candidates: list[dict[str, str]],
    *,
    region: str,
    locality: str,
    address: str,
    sample_limit: int = 0,
) -> tuple[
    list[dict[str, str]],
    dict[str, int],
    list[RejectedCandidateDiagnostic],
]:
    accepted: list[dict[str, str]] = []
    reasons: Counter[str] = Counter()
    samples: list[RejectedCandidateDiagnostic] = []
    seen: set[str] = set()
    for candidate in candidates:
        number = normalize_cadastral_number(candidate.get("cadastral_number"))
        if not number or number in seen:
            reasons["недостаточно данных"] += 1
            continue
        reason = candidate_rejection_reason(
            candidate, region=region, locality=locality, address=address
        )
        if reason:
            reasons[reason] += 1
            if len(samples) < sample_limit:
                candidate_address = str(candidate.get("address", "")).strip()
                samples.append(
                    RejectedCandidateDiagnostic(
                        original_address=candidate_address[:1000],
                        normalized_candidate=_canonical_freeform_address(
                            candidate_address
                        )[:1000],
                        normalized_source=normalize_address(
                            region=region, locality=locality, address=address
                        )[:1000],
                        reason=reason,
                        source_street=_street(address),
                        candidate_street=_street(candidate_address),
                        source_house=_house(address, allow_trailing=True),
                        candidate_house=_house(
                            candidate_address, allow_trailing=True
                        ),
                        source_building=_qualifier(address, "стр|строение"),
                        candidate_building=_qualifier(
                            candidate_address, "стр|строение"
                        ),
                    )
                )
            continue
        seen.add(number)
        accepted.append({**candidate, "cadastral_number": number})
    return accepted, dict(reasons), samples


def filter_candidates(
    candidates: list[dict[str, str]],
    *,
    region: str,
    locality: str,
    address: str,
) -> list[dict[str, str]]:
    return filter_candidates_with_reasons(
        candidates, region=region, locality=locality, address=address
    )[0]


class PynspdCadastralSearcher:
    """Run one retry-free, TLS-verified pynspd request in a killable process."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if timeout <= 0:
            raise ValueError("invalid NSPD timeout setting")
        self.timeout = float(timeout)
        self._run = run

    def search(
        self,
        query: str,
        *,
        region: str,
        locality: str,
        address: str,
        timeout_seconds: float | None = None,
        timeout_reason: str = "request_timeout",
    ) -> list[dict[str, str]]:
        del region, locality, address
        requested_budget = timeout_seconds or self.timeout
        hard_timeout = min(self.timeout, requested_budget)
        effective_timeout_reason = (
            "request_timeout" if self.timeout < requested_budget else timeout_reason
        )
        if hard_timeout <= 0:
            raise NspdDeadlineExceeded(timeout_reason)
        worker_input = json.dumps(
            {"query": query, "timeout_seconds": hard_timeout}, ensure_ascii=False
        )
        worker_env = dict(os.environ)
        for name in tuple(worker_env):
            upper = name.upper()
            if upper.startswith("PYNSPD_") or any(
                marker in upper
                for marker in ("SECRET", "TOKEN", "AUTH_KEY", "PASSWORD")
            ):
                worker_env.pop(name, None)
        try:
            completed = self._run(
                [sys.executable, "-m", "news_super_analyser.nspd_worker"],
                input=worker_input,
                text=True,
                capture_output=True,
                timeout=hard_timeout,
                check=False,
                env=worker_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise NspdDeadlineExceeded(effective_timeout_reason) from exc
        if completed.returncode != 0:
            raise NspdWorkerError("worker_exit")
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NspdWorkerError("invalid_worker_response") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            error_type = (
                str(payload.get("error_type", "worker_error"))[:100]
                if isinstance(payload, dict)
                else "worker_error"
            )
            raise NspdWorkerError(error_type)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not all(
            isinstance(item, dict) for item in candidates
        ):
            raise NspdWorkerError("invalid_worker_candidates")
        return [
            {
                "cadastral_number": str(item.get("cadastral_number", "")),
                "address": str(item.get("address", "")),
                "category": str(item.get("category", "")),
                "category_name": str(item.get("category_name", "")),
                "building_name": str(item.get("building_name", "")),
                "purpose": str(item.get("purpose", "")),
                "permitted_use": str(item.get("permitted_use", "")),
                "cadastral_quarter": str(item.get("cadastral_quarter", "")),
            }
            for item in candidates
        ]


class CadastralEnrichmentProcessor:
    def __init__(
        self,
        gateway: ObjectEnrichmentGateway,
        searcher: CadastralSearcher,
        *,
        logger: logging.Logger | None = None,
        attempts: int = 2,
        request_delay: float = 1.0,
        row_timeout_seconds: float = 30.0,
        stage_timeout_seconds: float = 120.0,
        rejection_diagnostics: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            attempts < 1
            or request_delay < 0
            or row_timeout_seconds <= 0
            or stage_timeout_seconds <= 0
            or row_timeout_seconds > stage_timeout_seconds
        ):
            raise ValueError("invalid retry/delay settings")
        self.gateway = gateway
        self.searcher = searcher
        self.logger = logger or logging.getLogger(__name__)
        self.attempts = attempts
        self.request_delay = request_delay
        self.row_timeout_seconds = row_timeout_seconds
        self.stage_timeout_seconds = stage_timeout_seconds
        self.rejection_diagnostics = rejection_diagnostics
        self.sleep = sleep
        self.monotonic = monotonic

    def run(
        self,
        limit: int,
        *,
        row_number: int | None = None,
        apply: bool = False,
    ) -> EnrichmentStats:
        stage_started = self.monotonic()
        stage_deadline = stage_started + self.stage_timeout_seconds
        self.logger.info(
            "TG-08.1 начат; HTTP timeout=%.1fс лимит строки=%.1fс "
            "лимит этапа=%.1fс попыток=%d внутренних повторов=0 задержка=%.1fс",
            float(getattr(self.searcher, "timeout", 0.0) or 0.0),
            self.row_timeout_seconds,
            self.stage_timeout_seconds,
            self.attempts,
            self.request_delay,
        )
        candidates = self.gateway.read_object_enrichment_candidates(
            limit, row_number=row_number
        )
        stats = EnrichmentStats()
        for index, row in enumerate(candidates):
            if self.monotonic() >= stage_deadline:
                deferred = len(candidates) - index
                stats.deferred_rows += deferred
                stats.errors += deferred
                stats.row_errors.append(
                    f"лимит кадастрового этапа: отложено строк {deferred}"
                )
                self.logger.warning(
                    "TG-08.1 лимит этапа исчерпан; отложено строк=%d", deferred
                )
                break
            stats.checked += 1
            row_started = self.monotonic()
            row_deadline = min(
                stage_deadline, row_started + self.row_timeout_seconds
            )
            try:
                values, prepare_gemini, found, diagnostic, search_error = self._plan(
                    row,
                    row_deadline=row_deadline,
                    stage_deadline=stage_deadline,
                )
                stats.search_diagnostics.append(diagnostic)
                stats.nspd_queries += sum(item.requests for item in diagnostic.attempts)
                stats.cadastral_numbers_found += found
                stats.existing_numbers += bool(
                    normalize_cadastral_number(row.get("cadastral_number"))
                )
                if search_error:
                    stats.errors += 1
                    if diagnostic.outcome in {"row_timeout", "stage_timeout"}:
                        stats.timed_out_rows += 1
                    stats.row_errors.append(
                        f"строка {row.get('row_number', '?')}: "
                        f"{diagnostic.outcome}"
                    )
                stats.cells_planned += len(values) + int(prepare_gemini)
                if values or prepare_gemini:
                    plan: dict[str, Any] = {
                        "row_number": row.get("row_number", "?"),
                        **values,
                    }
                    if prepare_gemini:
                        plan["gemini"] = build_object_gemini_formula(
                            int(row["row_number"])
                        )
                    stats.planned_rows.append(plan)
                if apply and (values or prepare_gemini):
                    if self.monotonic() >= stage_deadline:
                        stats.deferred_rows += 1
                        self.logger.warning(
                            "TG-08.1 строка=%s отложена: лимит этапа исчерпан "
                            "до записи",
                            row.get("row_number", "?"),
                        )
                    else:
                        result = self.gateway.update_object_enrichment(
                            analysis_id=str(row["analysis_id"]),
                            object_number=str(row["object_number"]),
                            values=values,
                            prepare_gemini=prepare_gemini,
                        )
                        updated = result.get("updated_fields", [])
                        stats.cells_updated += len(updated)
                        stats.rows_updated += bool(updated)
                if not search_error:
                    stats.successful_rows += 1
                self.logger.info(
                    "TG-08.1 строка=%s outcome=%s HTTP-попыток=%d найдено=%d "
                    "время=%.2fс",
                    diagnostic.row_number,
                    diagnostic.outcome,
                    sum(item.requests for item in diagnostic.attempts),
                    diagnostic.matching_numbers,
                    self.monotonic() - row_started,
                )
            except Exception as exc:
                stats.errors += 1
                label = f"строка {row.get('row_number', '?')}: {type(exc).__name__}"
                stats.row_errors.append(label)
                self.logger.exception("Ошибка TG-08.1 для %s", label)
            if self.request_delay and index + 1 < len(candidates):
                remaining = stage_deadline - self.monotonic()
                if remaining > 0:
                    self.sleep(min(self.request_delay, remaining))
        self.logger.info(
            "TG-08.1 завершён; время=%.2fс проверено=%d HTTP-попыток=%d "
            "успешно=%d найдено=%d уже было=%d ошибок=%d тайм-аутов=%d "
            "отложено=%d",
            self.monotonic() - stage_started,
            stats.checked,
            stats.nspd_queries,
            stats.successful_rows,
            stats.cadastral_numbers_found,
            stats.existing_numbers,
            stats.errors,
            stats.timed_out_rows,
            stats.deferred_rows,
        )
        return stats

    def _plan(
        self,
        row: dict[str, Any],
        *,
        row_deadline: float,
        stage_deadline: float,
    ) -> tuple[dict[str, str], bool, int, RowSearchDiagnostic, bool]:
        values: dict[str, str] = {}
        current_j = str(row.get("cadastral_number", ""))
        current_o = str(row.get("cadastral_quarter", ""))
        current_p = str(row.get("all_cadastral", ""))
        current_q = str(row.get("gemini", ""))
        locality = str(row.get("locality", "")).strip()
        address = str(row.get("address", "")).strip()
        region = str(row.get("region", "")).strip()
        diagnostic = RowSearchDiagnostic(row_number=row.get("row_number", "?"))

        known_number = normalize_cadastral_number(current_j)
        if not current_o and known_number:
            values["cadastral_quarter"] = cadastral_quarter(known_number)
        if not current_p and known_number:
            values["all_cadastral"] = known_number

        needs_search = bool(
            locality
            and address
            and not known_number
            and any(not value for value in (current_j, current_o, current_p))
            and is_specific_address(address)
        )
        matches: list[dict[str, str]] = []
        search_error = False
        if needs_search:
            matches, diagnostic, search_error = self._search_cascade(
                row_number=row.get("row_number", "?"),
                region=region,
                locality=locality,
                address=address,
                row_deadline=row_deadline,
                stage_deadline=stage_deadline,
            )

        candidates_by_number: dict[str, dict[str, str]] = {}
        for item in matches:
            number = normalize_cadastral_number(item.get("cadastral_number"))
            if number and number not in candidates_by_number:
                candidates_by_number[number] = item
        numbers = sorted(candidates_by_number)
        quarters = sorted(
            {
                normalize_cadastral_quarter(
                    candidates_by_number[number].get("cadastral_quarter")
                )
                or cadastral_quarter(number)
                for number in numbers
            }
        )
        diagnostic.matching_numbers = len(numbers)
        if not current_p and numbers:
            values["all_cadastral"] = "\n".join(
                format_cadastral_candidate(candidates_by_number[number])
                for number in numbers
            )
        if not current_j and len(numbers) == 1:
            values["cadastral_number"] = numbers[0]
        if (
            not current_o
            and "cadastral_quarter" not in values
            and len(quarters) == 1
        ):
            values["cadastral_quarter"] = quarters[0]

        prepare_gemini = bool(
            not current_q
            and locality
            and (
                str(row.get("organization", "")).strip()
                or str(row.get("name", "")).strip()
            )
        )
        return values, prepare_gemini, len(numbers), diagnostic, search_error

    def _search_cascade(
        self,
        *,
        row_number: int | str,
        region: str,
        locality: str,
        address: str,
        row_deadline: float,
        stage_deadline: float,
    ) -> tuple[list[dict[str, str]], RowSearchDiagnostic, bool]:
        diagnostic = RowSearchDiagnostic(row_number=row_number, outcome="not_found")
        remaining_samples = 5 if self.rejection_diagnostics else 0
        variants = build_search_variants(
            region=region, locality=locality, address=address
        )
        for index, (level, query) in enumerate(variants):
            raw, requests_count, error = self._search_with_retry(
                query,
                region=region,
                locality=locality,
                address=address,
                row_deadline=row_deadline,
                stage_deadline=stage_deadline,
            )
            if error is not None:
                status = _search_error_status(error)
                diagnostic.attempts.append(
                    SearchAttemptDiagnostic(
                        level=level,
                        query=query[:500],
                        requests=requests_count,
                        returned=0,
                        accepted=0,
                        rejected=0,
                        status=status,
                        error_type=_network_error_type(error),
                    )
                )
                diagnostic.outcome = status
                return [], diagnostic, True

            accepted, reasons, samples = filter_candidates_with_reasons(
                raw,
                region=region,
                locality=locality,
                address=address,
                sample_limit=remaining_samples,
            )
            remaining_samples -= len(samples)
            status = "matched" if accepted else ("all_rejected" if raw else "empty")
            diagnostic.attempts.append(
                SearchAttemptDiagnostic(
                    level=level,
                    query=query[:500],
                    requests=requests_count,
                    returned=len(raw),
                    accepted=len(accepted),
                    rejected=len(raw) - len(accepted),
                    rejection_reasons=reasons,
                    status=status,
                    rejected_samples=samples,
                )
            )
            if accepted:
                diagnostic.outcome = "matched"
                return accepted, diagnostic, False
            if self.request_delay and index + 1 < len(variants):
                remaining, _ = self._remaining_budget(
                    row_deadline=row_deadline, stage_deadline=stage_deadline
                )
                if remaining <= 0:
                    diagnostic.outcome = self._deadline_reason(
                        row_deadline=row_deadline, stage_deadline=stage_deadline
                    )
                    return [], diagnostic, True
                self.sleep(min(self.request_delay, remaining))
        return [], diagnostic, False

    def _search_with_retry(
        self,
        query: str,
        *,
        region: str,
        locality: str,
        address: str,
        row_deadline: float,
        stage_deadline: float,
    ) -> tuple[list[dict[str, str]], int, Exception | None]:
        for attempt in range(1, self.attempts + 1):
            remaining, deadline_reason = self._remaining_budget(
                row_deadline=row_deadline, stage_deadline=stage_deadline
            )
            if remaining <= 0:
                return [], attempt - 1, NspdDeadlineExceeded(deadline_reason)
            try:
                return (
                    self.searcher.search(
                        query,
                        region=region,
                        locality=locality,
                        address=address,
                        timeout_seconds=remaining,
                        timeout_reason=deadline_reason,
                    ),
                    attempt,
                    None,
                )
            except Exception as exc:
                if isinstance(exc, NspdDeadlineExceeded) and exc.reason in {
                    "row_timeout",
                    "stage_timeout",
                }:
                    return [], attempt, exc
                if attempt == self.attempts:
                    return [], attempt, exc
                self.logger.warning(
                    "NSPD не ответила; тип=%s HTTP-попытка=%d/%d",
                    _network_error_type(exc),
                    attempt,
                    self.attempts,
                )
                remaining, _ = self._remaining_budget(
                    row_deadline=row_deadline, stage_deadline=stage_deadline
                )
                if remaining <= 0:
                    continue
                self.sleep(
                    min(remaining, min(2.0, max(0.1, self.request_delay)))
                )
        raise AssertionError("unreachable")

    def _remaining_budget(
        self, *, row_deadline: float, stage_deadline: float
    ) -> tuple[float, str]:
        now = self.monotonic()
        row_remaining = row_deadline - now
        stage_remaining = stage_deadline - now
        reason = "stage_timeout" if stage_remaining <= row_remaining else "row_timeout"
        return min(row_remaining, stage_remaining), reason

    def _deadline_reason(
        self, *, row_deadline: float, stage_deadline: float
    ) -> str:
        return self._remaining_budget(
            row_deadline=row_deadline, stage_deadline=stage_deadline
        )[1]


def _search_error_status(error: Exception) -> str:
    if isinstance(error, NspdDeadlineExceeded):
        return error.reason
    if isinstance(error, NspdWorkerError):
        return "nspd_unavailable"
    marker = f"{type(error).__name__} {error}".lower()
    if any(
        word in marker
        for word in ("timeout", "connection", "network", "http", "unavailable")
    ):
        return "nspd_unavailable"
    return "response_error"


def _network_error_type(error: Exception) -> str:
    if isinstance(error, NspdWorkerError):
        return error.error_type or type(error).__name__
    return type(error).__name__


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
