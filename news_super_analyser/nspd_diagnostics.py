"""One-shot, isolated network diagnostics for the public NSPD service."""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import queue
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .safe_logging import RedactingFilter, RedactingFormatter, SecretRedactor


HOST = "nspd.gov.ru"
PORT = 443
ROOT_URL = "https://nspd.gov.ru/"
API_URL = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
DIAGNOSTIC_BUDGET_SECONDS = 60.0
DEFAULT_MARKER = Path(tempfile.gettempdir()) / "news_super_analyser_nspd_diag.done"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str
    duration_ms: int
    error_type: str = ""
    details: dict[str, str | int] = field(default_factory=dict)
    response_received: bool = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def diagnostic_mode_enabled() -> bool:
    """Only the explicit value ``1`` enables the isolated mode."""

    return os.getenv("NSPD_DIAGNOSTIC_MODE", "").strip() == "1"


def configure_diagnostic_logging() -> logging.Logger:
    """Create a stdout-only logger and redact all secret-like environment values."""

    secret_markers = ("SECRET", "TOKEN", "PASSWORD", "AUTH_KEY", "API_KEY")
    secrets = (
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in secret_markers)
    )
    redactor = SecretRedactor(secrets)
    logger = logging.getLogger("news_super_analyser.nspd_diagnostics")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(message)s", redactor))
    handler.addFilter(RedactingFilter(redactor))
    logger.addHandler(handler)
    return logger


def _bounded_call(function: Callable[[], Any], timeout: float) -> Any:
    """Run a possibly blocking resolver call behind a hard daemon-thread deadline."""

    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            results.put((True, function()))
        except BaseException as exc:  # transported to the diagnostic coordinator
            results.put((False, exc))

    worker = threading.Thread(target=target, daemon=True, name="nspd-diag-probe")
    worker.start()
    try:
        ok, value = results.get(timeout=max(0.001, timeout))
    except queue.Empty as exc:
        raise TimeoutError("probe deadline exceeded") from exc
    if ok:
        return value
    raise value


def _duration_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _dns_probe(timeout: float, monotonic: Callable[[], float]) -> tuple[ProbeResult, list[str]]:
    started = monotonic()
    try:
        records = _bounded_call(
            lambda: socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM), timeout
        )
        addresses = list(dict.fromkeys(item[4][0] for item in records))
        if not addresses:
            raise socket.gaierror("no addresses")
        return (
            ProbeResult(
                "DNS",
                "OK",
                _duration_ms(started, monotonic),
                details={"addresses": len(addresses)},
            ),
            addresses,
        )
    except TimeoutError:
        return ProbeResult("DNS", "ERROR", _duration_ms(started, monotonic), "DNS_TIMEOUT"), []
    except socket.gaierror:
        return ProbeResult("DNS", "ERROR", _duration_ms(started, monotonic), "DNS_FAILURE"), []
    except Exception as exc:
        return ProbeResult("DNS", "ERROR", _duration_ms(started, monotonic), type(exc).__name__), []


def _tcp_probe(address: str, timeout: float, monotonic: Callable[[], float]) -> ProbeResult:
    started = monotonic()
    try:
        with socket.create_connection((address, PORT), timeout=timeout):
            pass
        return ProbeResult("TCP", "OK", _duration_ms(started, monotonic))
    except (TimeoutError, socket.timeout):
        return ProbeResult("TCP", "ERROR", _duration_ms(started, monotonic), "TCP_TIMEOUT")
    except ConnectionRefusedError:
        return ProbeResult("TCP", "ERROR", _duration_ms(started, monotonic), "CONNECTION_REFUSED")
    except OSError as exc:
        return ProbeResult("TCP", "ERROR", _duration_ms(started, monotonic), type(exc).__name__)


def _tls_probe(address: str, timeout: float, monotonic: Callable[[], float]) -> ProbeResult:
    started = monotonic()
    raw_socket = None
    try:
        context = ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise RuntimeError("verified TLS context unavailable")
        raw_socket = socket.create_connection((address, PORT), timeout=timeout)
        remaining = max(0.001, timeout - (monotonic() - started))
        raw_socket.settimeout(remaining)
        with context.wrap_socket(raw_socket, server_hostname=HOST) as tls_socket:
            raw_socket = None
            version = tls_socket.version() or "unknown"
        return ProbeResult(
            "TLS", "OK", _duration_ms(started, monotonic), details={"tls_version": version}
        )
    except ssl.SSLCertVerificationError:
        return ProbeResult(
            "TLS",
            "ERROR",
            _duration_ms(started, monotonic),
            "TLS_CERTIFICATE_VERIFICATION_FAILURE",
        )
    except ssl.SSLError:
        return ProbeResult(
            "TLS", "ERROR", _duration_ms(started, monotonic), "TLS_HANDSHAKE_FAILURE"
        )
    except (TimeoutError, socket.timeout):
        return ProbeResult("TLS", "ERROR", _duration_ms(started, monotonic), "TLS_TIMEOUT")
    except Exception as exc:
        return ProbeResult("TLS", "ERROR", _duration_ms(started, monotonic), type(exc).__name__)
    finally:
        if raw_socket is not None:
            raw_socket.close()


def _pynspd_headers() -> dict[str, str]:
    from pynspd.client import BaseNspdClient

    return BaseNspdClient._get_headers()


def _open_once(url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any, bytes]:
    """Perform exactly one verified HTTPS request, without redirects or retries."""

    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context unavailable")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read(65_537)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.headers, exc.read(65_537)
        finally:
            exc.close()


def _http_error_type(exc: BaseException) -> str:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS_CERTIFICATE_VERIFICATION_FAILURE"
    if isinstance(reason, ssl.SSLError):
        return "TLS_HANDSHAKE_FAILURE"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "HTTP_TIMEOUT"
    if isinstance(reason, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    return type(reason).__name__


def _status_error_type(status: int) -> str:
    if status in {400, 405}:
        return "INVALID_REQUEST"
    if status == 403:
        return "HTTP_FORBIDDEN"
    if status == 429:
        return "HTTP_RATE_LIMITED"
    if status >= 500:
        return "HTTP_SERVER_ERROR"
    if status >= 400:
        return "HTTP_CLIENT_ERROR"
    return ""


def _http_probe(
    name: str,
    url: str,
    headers: dict[str, str],
    timeout: float,
    monotonic: Callable[[], float],
    *,
    expect_json: bool = False,
) -> ProbeResult:
    started = monotonic()
    try:
        status, response_headers, body = _bounded_call(
            lambda: _open_once(url, headers, timeout), timeout
        )
        details: dict[str, str | int] = {"http_status": status}
        location = response_headers.get("Location", "") if response_headers else ""
        if 300 <= status < 400:
            details["redirect"] = "yes" if location else "no"
        error_type = _status_error_type(status)
        if not error_type and expect_json and 200 <= status < 300:
            if len(body) > 65_536:
                error_type = "RESPONSE_PARSING_FAILURE"
            else:
                try:
                    json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    error_type = "RESPONSE_PARSING_FAILURE"
        return ProbeResult(
            name,
            str(status),
            _duration_ms(started, monotonic),
            error_type,
            details,
            response_received=True,
        )
    except Exception as exc:
        return ProbeResult(
            name,
            "ERROR",
            _duration_ms(started, monotonic),
            _http_error_type(exc),
        )


def _pynspd_probe(monotonic: Callable[[], float]) -> ProbeResult:
    started = monotonic()
    try:
        version = importlib.metadata.version("pynspd")
        headers = _pynspd_headers()
        timeout = os.getenv("NSPD_CLIENT_TIMEOUT", "10").strip() or "10"
        details: dict[str, str | int] = {
            "version": version,
            "method": "GET",
            "headers": ",".join(sorted(headers)),
            "timeout": timeout,
            "retries": 0,
            "tls_verify": "required",
            "trust_env": "false",
            "pynspd_env": "ignored",
            "errors": "class_only_in_worker",
        }
        return ProbeResult("PYNSPD", "OK", _duration_ms(started, monotonic), details=details)
    except Exception as exc:
        return ProbeResult(
            "PYNSPD", "ERROR", _duration_ms(started, monotonic), type(exc).__name__
        )


def _skipped(name: str, reason: str) -> ProbeResult:
    return ProbeResult(name, "SKIPPED", 0, details={"reason": reason})


def _format_result(result: ProbeResult) -> str:
    fields = [f"NSPD-DIAG {result.name}", f"status={result.status}"]
    fields.extend(f"{key}={value}" for key, value in result.details.items())
    if result.error_type:
        fields.append(f"error_type={result.error_type}")
    fields.append(f"duration_ms={result.duration_ms}")
    return " ".join(fields)


def _probable_failure_layer(results: list[ProbeResult]) -> str:
    by_name = {result.name: result for result in results}
    for layer in ("DNS", "TCP", "TLS"):
        result = by_name.get(layer)
        if result and result.status == "ERROR":
            return layer
    http = by_name.get("HTTP")
    if http and (http.status == "ERROR" or http.error_type or http.status.startswith(("4", "5"))):
        return "HTTP"
    api = by_name.get("API")
    if api and (api.status == "ERROR" or api.error_type or api.status.startswith(("4", "5"))):
        return "API"
    return "INCONCLUSIVE"


def _remaining_timeout(
    deadline: float, per_probe: float, monotonic: Callable[[], float]
) -> float:
    return max(0.0, min(per_probe, deadline - monotonic()))


def run_diagnostics(
    *,
    logger: logging.Logger | None = None,
    marker_path: Path = DEFAULT_MARKER,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Run the diagnostic once and return without entering the monitoring service."""

    logger = logger or configure_diagnostic_logging()
    logger.info("NSPD-DIAG START")
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError:
        logger.info("NSPD-DIAG SKIPPED status=SKIPPED reason=already_completed")
        logger.info("NSPD-DIAG SUMMARY probable_failure_layer=INCONCLUSIVE")
        logger.info("NSPD-DIAG END")
        return 0
    except OSError:
        # Diagnostics remain usable on a read-only temporary filesystem. The
        # BotHost instructions still require a stopped, controlled one-shot run.
        logger.info("NSPD-DIAG GUARD status=UNAVAILABLE error_type=OSError")

    deadline = monotonic() + DIAGNOSTIC_BUDGET_SECONDS
    results: list[ProbeResult] = []

    dns_timeout = _remaining_timeout(deadline, 5.0, monotonic)
    dns, addresses = _dns_probe(dns_timeout, monotonic)
    results.append(dns)
    logger.info(_format_result(dns))

    if not addresses:
        for name in ("TCP", "TLS", "HTTP", "API"):
            result = _skipped(name, "dns_unavailable")
            results.append(result)
            logger.info(_format_result(result))
    else:
        tcp_timeout = _remaining_timeout(deadline, 8.0, monotonic)
        tcp = (
            _tcp_probe(addresses[0], tcp_timeout, monotonic)
            if tcp_timeout > 0
            else _skipped("TCP", "global_budget_exhausted")
        )
        results.append(tcp)
        logger.info(_format_result(tcp))

        if tcp.status != "OK":
            for name in ("TLS", "HTTP", "API"):
                result = _skipped(name, "tcp_unavailable")
                results.append(result)
                logger.info(_format_result(result))
        else:
            tls_timeout = _remaining_timeout(deadline, 10.0, monotonic)
            tls = (
                _tls_probe(addresses[0], tls_timeout, monotonic)
                if tls_timeout > 0
                else _skipped("TLS", "global_budget_exhausted")
            )
            results.append(tls)
            logger.info(_format_result(tls))

            if tls.status != "OK":
                for name in ("HTTP", "API"):
                    result = _skipped(name, "tls_unavailable")
                    results.append(result)
                    logger.info(_format_result(result))
            else:
                headers = _pynspd_headers()
                http_timeout = _remaining_timeout(deadline, 10.0, monotonic)
                http = (
                    _http_probe("HTTP", ROOT_URL, headers, http_timeout, monotonic)
                    if http_timeout > 0
                    else _skipped("HTTP", "global_budget_exhausted")
                )
                results.append(http)
                logger.info(_format_result(http))

                api_timeout = _remaining_timeout(deadline, 10.0, monotonic)
                if api_timeout > 0:
                    query = urllib.parse.urlencode(
                        {
                            "query": "NSPD diagnostic connectivity test",
                            "thematicSearchId": "1",
                        }
                    )
                    api = _http_probe(
                        "API",
                        f"{API_URL}?{query}",
                        headers,
                        api_timeout,
                        monotonic,
                        expect_json=True,
                    )
                else:
                    api = _skipped("API", "global_budget_exhausted")
                results.append(api)
                logger.info(_format_result(api))

    pynspd_result = _pynspd_probe(monotonic)
    results.append(pynspd_result)
    logger.info(_format_result(pynspd_result))
    logger.info(
        "NSPD-DIAG SUMMARY probable_failure_layer=%s",
        _probable_failure_layer(results),
    )
    logger.info("NSPD-DIAG END")
    return 0
