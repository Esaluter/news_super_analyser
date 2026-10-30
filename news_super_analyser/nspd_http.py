"""Small TLS-verified NSPD HTTP client used only by the local queue worker."""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .cadastral_enrichment import normalize_cadastral_number


HOST = "nspd.gov.ru"
API_URL = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
MAX_RESPONSE_BYTES = 256_000


class NspdHttpError(ConnectionError):
    def __init__(self, reason: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(reason)


class NspdResponseRecorder(Protocol):
    max_response_bytes: int

    def capture_http(self, **values: Any) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def build_verified_nspd_context() -> ssl.SSLContext:
    """Use platform trust plus the official CA files already shipped by the project."""

    context = ssl.create_default_context()
    try:
        import certifi  # type: ignore[import-not-found]

        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    certificate_directory = Path(__file__).resolve().parents[1] / "certificates"
    for name in (
        "russian_trusted_root_ca_2022.crt",
        "russian_trusted_sub_ca_2024.crt",
    ):
        path = certificate_directory / name
        if path.is_file():
            context.load_verify_locations(cafile=str(path))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context is unavailable")
    return context


class VerifiedNspdHttpSearcher:
    """Perform one direct, retry-free search with the standalone-tested endpoint."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        context: ssl.SSLContext | None = None,
        logger: logging.Logger | None = None,
        debug_recorder: NspdResponseRecorder | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("invalid NSPD timeout setting")
        self.timeout = float(timeout)
        self.context = context or build_verified_nspd_context()
        self.logger = logger or logging.getLogger(__name__)
        self.debug_recorder = debug_recorder

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
        timeout = min(self.timeout, timeout_seconds or self.timeout)
        if timeout <= 0:
            raise TimeoutError(timeout_reason)
        url = API_URL + "?" + urllib.parse.urlencode(
            {"query": query, "thematicSearchId": "1"}
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.context),
            _NoRedirect(),
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://nspd.gov.ru/map?thematic=PKK",
                "Host": HOST,
                "Accept": "application/json, text/plain, */*",
            },
            method="GET",
        )
        started = time.monotonic()
        read_limit = (
            self.debug_recorder.max_response_bytes
            if self.debug_recorder is not None
            else MAX_RESPONSE_BYTES
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(response.status)
                raw_body = response.read(read_limit + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            duration = round((time.monotonic() - started) * 1000)
            error_body, error_truncated = self._read_error_body(exc, read_limit)
            self._capture_http(
                query=query,
                url=url,
                status=status,
                duration_ms=duration,
                body=error_body,
                body_truncated=error_truncated,
                error=f"HTTP_{status}",
            )
            self.logger.warning(
                "NSPD HTTP status=%d duration_ms=%d",
                status,
                duration,
            )
            if status == 404:
                return []
            raise NspdHttpError(f"HTTP_{status}", status=status) from exc
        except Exception as exc:
            duration = round((time.monotonic() - started) * 1000)
            self._capture_http(
                query=query,
                url=url,
                status=None,
                duration_ms=duration,
                body=None,
                body_truncated=False,
                error=type(exc).__name__,
            )
            self.logger.warning(
                "NSPD HTTP status=ERROR error_type=%s duration_ms=%d",
                type(exc).__name__,
                duration,
            )
            raise
        duration = round((time.monotonic() - started) * 1000)
        self.logger.info("NSPD HTTP status=%d duration_ms=%d", status, duration)
        body_truncated = len(raw_body) > read_limit
        body = raw_body[:read_limit]
        if len(raw_body) > MAX_RESPONSE_BYTES:
            self._capture_http(
                query=query,
                url=url,
                status=status,
                duration_ms=duration,
                body=body,
                body_truncated=body_truncated,
                error="response_too_large",
            )
            raise NspdHttpError("response_too_large", status=status)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._capture_http(
                query=query,
                url=url,
                status=status,
                duration_ms=duration,
                body=body,
                body_truncated=body_truncated,
                error="invalid_json",
            )
            raise NspdHttpError("invalid_json", status=status) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            self._capture_http(
                query=query,
                url=url,
                status=status,
                duration_ms=duration,
                body=body,
                body_truncated=body_truncated,
                error="invalid_response_schema",
            )
            raise NspdHttpError("invalid_response_schema", status=status)
        self._capture_http(
            query=query,
            url=url,
            status=status,
            duration_ms=duration,
            body=body,
            body_truncated=body_truncated,
            error=None,
        )
        return _extract_candidates(features)

    def _read_error_body(
        self, error: urllib.error.HTTPError, read_limit: int
    ) -> tuple[bytes | None, bool]:
        if self.debug_recorder is None:
            return None, False
        try:
            body = error.read(read_limit + 1)
        except Exception:
            return None, False
        return body[:read_limit], len(body) > read_limit

    def _capture_http(
        self,
        *,
        query: str,
        url: str,
        status: int | None,
        duration_ms: int,
        body: bytes | None,
        body_truncated: bool,
        error: str | None,
    ) -> None:
        if self.debug_recorder is None:
            return
        try:
            self.debug_recorder.capture_http(
                query=query,
                url=url,
                method="GET",
                status=status,
                duration_ms=duration_ms,
                body=body,
                body_truncated=body_truncated,
                error=error,
            )
        except Exception as exc:
            self.logger.error(
                "NSPD debug capture failed; error_type=%s", type(exc).__name__
            )


def _extract_candidates(features: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        options = properties.get("options") if isinstance(properties, dict) else None
        if not isinstance(options, dict):
            continue
        number = normalize_cadastral_number(
            options.get("cad_number") or options.get("cad_num")
        )
        if not number or number in seen:
            continue
        seen.add(number)
        result.append(
            {
                "cadastral_number": number,
                "address": str(
                    options.get("readable_address")
                    or options.get("address_readable_address")
                    or ""
                ).strip(),
                "category": str(properties.get("category") or ""),
                "category_name": str(properties.get("category_name") or ""),
            }
        )
    return result
