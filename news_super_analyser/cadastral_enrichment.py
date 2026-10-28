"""TG-08.1 cadastral enrichment for rows of the ``ОБЪЕКТЫ`` sheet."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


CADASTRAL_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,7}):(\d+)$")
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
        self, query: str, *, region: str, locality: str, address: str
    ) -> list[dict[str, str]]: ...


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
        properties = getattr(feature, "properties", None)
        options = getattr(properties, "options", None) if properties else None
        if not options:
            continue
        number = normalize_cadastral_number(
            getattr(options, "cad_number", None)
            or getattr(options, "cad_num", None)
        )
        if not number or number in seen:
            continue
        address = (
            getattr(options, "readable_address", None)
            or getattr(options, "address_readable_address", None)
            or ""
        )
        seen.add(number)
        result.append(
            {
                "cadastral_number": number,
                "address": str(address).strip(),
                "category": str(getattr(properties, "category", None) or ""),
                "category_name": str(
                    getattr(properties, "category_name", None) or ""
                ),
            }
        )
    return result


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
    def __init__(self, *, timeout: int | None = None, retries: int | None = None) -> None:
        self.timeout = timeout if timeout is not None else int(
            os.getenv("NSPD_CLIENT_TIMEOUT", "20")
        )
        self.retries = retries if retries is not None else int(
            os.getenv("NSPD_CLIENT_RETRIES", "1")
        )
        if self.timeout < 1 or self.retries < 0 or self.retries > 3:
            raise ValueError("invalid NSPD timeout/retry settings")

    def search(
        self, query: str, *, region: str, locality: str, address: str
    ) -> list[dict[str, str]]:
        from pynspd import Nspd

        client = Nspd(client_timeout=self.timeout, client_retries=self.retries)
        try:
            return extract_candidates(client.search(query))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


class CadastralEnrichmentProcessor:
    def __init__(
        self,
        gateway: ObjectEnrichmentGateway,
        searcher: CadastralSearcher,
        *,
        logger: logging.Logger | None = None,
        attempts: int = 2,
        request_delay: float = 1.0,
        rejection_diagnostics: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1 or request_delay < 0:
            raise ValueError("invalid retry/delay settings")
        self.gateway = gateway
        self.searcher = searcher
        self.logger = logger or logging.getLogger(__name__)
        self.attempts = attempts
        self.request_delay = request_delay
        self.rejection_diagnostics = rejection_diagnostics
        self.sleep = sleep

    def run(
        self,
        limit: int,
        *,
        row_number: int | None = None,
        apply: bool = False,
    ) -> EnrichmentStats:
        candidates = self.gateway.read_object_enrichment_candidates(
            limit, row_number=row_number
        )
        stats = EnrichmentStats()
        for index, row in enumerate(candidates):
            stats.checked += 1
            try:
                values, prepare_gemini, found, diagnostic, search_error = self._plan(row)
                stats.search_diagnostics.append(diagnostic)
                stats.nspd_queries += sum(item.requests for item in diagnostic.attempts)
                stats.cadastral_numbers_found += found
                if search_error:
                    stats.errors += 1
                    stats.row_errors.append(
                        f"строка {row.get('row_number', '?')}: "
                        f"{diagnostic.attempts[-1].status}"
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
                    result = self.gateway.update_object_enrichment(
                        analysis_id=str(row["analysis_id"]),
                        object_number=str(row["object_number"]),
                        values=values,
                        prepare_gemini=prepare_gemini,
                    )
                    updated = result.get("updated_fields", [])
                    stats.cells_updated += len(updated)
                    stats.rows_updated += bool(updated)
                self.logger.info(
                    "TG-08.1 строка=%s outcome=%s запросов=%d найдено=%d",
                    diagnostic.row_number,
                    diagnostic.outcome,
                    sum(item.requests for item in diagnostic.attempts),
                    diagnostic.matching_numbers,
                )
            except Exception as exc:
                stats.errors += 1
                label = f"строка {row.get('row_number', '?')}: {type(exc).__name__}"
                stats.row_errors.append(label)
                self.logger.exception("Ошибка TG-08.1 для %s", label)
            if self.request_delay and index + 1 < len(candidates):
                self.sleep(self.request_delay)
        return stats

    def _plan(
        self, row: dict[str, Any]
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

        if not current_o and normalize_cadastral_number(current_j):
            values["cadastral_quarter"] = cadastral_quarter(current_j)

        needs_search = bool(
            locality
            and address
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
            )

        numbers = sorted(
            {
                normalize_cadastral_number(item.get("cadastral_number"))
                for item in matches
                if normalize_cadastral_number(item.get("cadastral_number"))
            }
        )
        quarters = sorted({cadastral_quarter(number) for number in numbers})
        diagnostic.matching_numbers = len(numbers)
        if not current_p and numbers:
            values["all_cadastral"] = "\n".join(numbers)
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
    ) -> tuple[list[dict[str, str]], RowSearchDiagnostic, bool]:
        diagnostic = RowSearchDiagnostic(row_number=row_number, outcome="not_found")
        remaining_samples = 5 if self.rejection_diagnostics else 0
        variants = build_search_variants(
            region=region, locality=locality, address=address
        )
        for index, (level, query) in enumerate(variants):
            raw, requests_count, error = self._search_with_retry(
                query, region=region, locality=locality, address=address
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
                        error_type=type(error).__name__,
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
                self.sleep(self.request_delay)
        return [], diagnostic, False

    def _search_with_retry(
        self, query: str, *, region: str, locality: str, address: str
    ) -> tuple[list[dict[str, str]], int, Exception | None]:
        for attempt in range(1, self.attempts + 1):
            try:
                return (
                    self.searcher.search(
                        query, region=region, locality=locality, address=address
                    ),
                    attempt,
                    None,
                )
            except Exception as exc:
                if attempt == self.attempts:
                    return [], attempt, exc
                self.logger.warning(
                    "NSPD не ответила, повтор %d/%d", attempt + 1, self.attempts
                )
                self.sleep(min(2.0, max(0.1, self.request_delay)))
        raise AssertionError("unreachable")


def _search_error_status(error: Exception) -> str:
    marker = f"{type(error).__name__} {error}".lower()
    if any(
        word in marker
        for word in ("timeout", "connection", "network", "http", "unavailable")
    ):
        return "nspd_unavailable"
    return "response_error"
