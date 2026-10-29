from __future__ import annotations

import io
import logging
import socket
import ssl
import time
from pathlib import Path

import pytest

from news_super_analyser import monitoring_app
from news_super_analyser import nspd_diagnostics as subject


def _logger(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(f"test.nspd.diag.{id(stream)}")
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


@pytest.fixture
def marker_path(request):
    path = Path(__file__).parent / f".{request.node.name}.marker"
    path.unlink(missing_ok=True)
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 01 "])
def test_diagnostic_mode_is_disabled_unless_value_is_exactly_one(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NSPD_DIAGNOSTIC_MODE", raising=False)
    else:
        monkeypatch.setenv("NSPD_DIAGNOSTIC_MODE", value)

    assert subject.diagnostic_mode_enabled() is False


def test_diagnostic_mode_accepts_explicit_one(monkeypatch):
    monkeypatch.setenv("NSPD_DIAGNOSTIC_MODE", " 1 ")
    assert subject.diagnostic_mode_enabled() is True


def test_monitoring_entrypoint_exits_through_diagnostics_before_configuration(monkeypatch):
    monkeypatch.setenv("NSPD_DIAGNOSTIC_MODE", "1")
    calls = []
    monkeypatch.setattr(subject, "run_diagnostics", lambda: calls.append("diag") or 0)
    monkeypatch.setattr(
        monitoring_app.MonitoringSettings,
        "from_env",
        lambda: pytest.fail("Google/Telegram configuration must not be loaded"),
    )
    monkeypatch.setattr(
        monitoring_app,
        "build_service",
        lambda *args, **kwargs: pytest.fail("main bot must not be built"),
    )

    assert monitoring_app.main() == 0
    assert calls == ["diag"]


def test_dns_failure_and_timeout_are_classified(monkeypatch):
    monkeypatch.setattr(
        subject.socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(socket.gaierror()),
    )
    failed, addresses = subject._dns_probe(0.1, subject.time.monotonic)
    assert failed.error_type == "DNS_FAILURE"
    assert addresses == []

    monkeypatch.setattr(
        subject,
        "_bounded_call",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    timed_out, _ = subject._dns_probe(0.1, subject.time.monotonic)
    assert timed_out.error_type == "DNS_TIMEOUT"


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (TimeoutError(), "TCP_TIMEOUT"),
        (ConnectionRefusedError(), "CONNECTION_REFUSED"),
    ],
)
def test_tcp_errors_are_classified(monkeypatch, exception, expected):
    monkeypatch.setattr(
        subject.socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(exception),
    )
    result = subject._tcp_probe("192.0.2.1", 0.1, subject.time.monotonic)
    assert result.error_type == expected


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (ssl.SSLCertVerificationError(), "TLS_CERTIFICATE_VERIFICATION_FAILURE"),
        (ssl.SSLError(), "TLS_HANDSHAKE_FAILURE"),
    ],
)
def test_tls_errors_are_classified(monkeypatch, exception, expected):
    class RawSocket:
        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    class Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, *args, **kwargs):
            raise exception

    monkeypatch.setattr(subject.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(subject.socket, "create_connection", lambda *args, **kwargs: RawSocket())
    result = subject._tls_probe("192.0.2.1", 0.1, subject.time.monotonic)
    assert result.error_type == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (405, "INVALID_REQUEST"),
        (403, "HTTP_FORBIDDEN"),
        (429, "HTTP_RATE_LIMITED"),
        (503, "HTTP_SERVER_ERROR"),
    ],
)
def test_http_statuses_are_classified(monkeypatch, status, expected):
    monkeypatch.setattr(subject, "_open_once", lambda *args: (status, {}, b"{}"))
    result = subject._http_probe(
        "API", subject.API_URL, {}, 1.0, subject.time.monotonic, expect_json=True
    )
    assert result.status == str(status)
    assert result.error_type == expected
    assert result.response_received is True


def test_http_timeout_and_response_parsing_failure_are_distinct(monkeypatch):
    monkeypatch.setattr(
        subject,
        "_open_once",
        lambda *args: (_ for _ in ()).throw(TimeoutError()),
    )
    timeout = subject._http_probe("HTTP", subject.ROOT_URL, {}, 1, subject.time.monotonic)
    assert timeout.error_type == "HTTP_TIMEOUT"

    monkeypatch.setattr(subject, "_open_once", lambda *args: (200, {}, b"not-json"))
    parsing = subject._http_probe(
        "API", subject.API_URL, {}, 1, subject.time.monotonic, expect_json=True
    )
    assert parsing.error_type == "RESPONSE_PARSING_FAILURE"


def test_http_probe_has_a_hard_wall_clock_deadline(monkeypatch):
    monkeypatch.setattr(subject, "_open_once", lambda *args: time.sleep(0.2))
    started = time.monotonic()
    result = subject._http_probe(
        "HTTP", subject.ROOT_URL, {}, 0.01, subject.time.monotonic
    )
    assert time.monotonic() - started < 0.1
    assert result.error_type == "HTTP_TIMEOUT"


def test_one_run_uses_bounded_timeouts_and_calls_api_once(monkeypatch, marker_path):
    calls = []
    monkeypatch.setattr(
        subject,
        "_dns_probe",
        lambda timeout, monotonic: (
            calls.append(("DNS", timeout))
            or subject.ProbeResult("DNS", "OK", 1, details={"addresses": 1}),
            ["192.0.2.1"],
        ),
    )
    monkeypatch.setattr(
        subject,
        "_tcp_probe",
        lambda address, timeout, monotonic: calls.append(("TCP", timeout))
        or subject.ProbeResult("TCP", "OK", 1),
    )
    monkeypatch.setattr(
        subject,
        "_tls_probe",
        lambda address, timeout, monotonic: calls.append(("TLS", timeout))
        or subject.ProbeResult("TLS", "OK", 1, details={"tls_version": "TLSv1.3"}),
    )

    def http(name, url, headers, timeout, monotonic, **kwargs):
        calls.append((name, timeout))
        return subject.ProbeResult(name, "200", 1, details={"http_status": 200}, response_received=True)

    monkeypatch.setattr(subject, "_http_probe", http)
    monkeypatch.setattr(
        subject,
        "_pynspd_probe",
        lambda monotonic: subject.ProbeResult("PYNSPD", "OK", 1),
    )
    monkeypatch.setattr(subject, "_pynspd_headers", lambda: {"Host": subject.HOST})

    assert subject.run_diagnostics(
        logger=_logger(io.StringIO()), marker_path=marker_path
    ) == 0
    assert [name for name, _ in calls].count("API") == 1
    assert dict(calls)["DNS"] <= 5
    assert dict(calls)["TCP"] <= 8
    assert dict(calls)["TLS"] <= 10
    assert dict(calls)["HTTP"] <= 10
    assert dict(calls)["API"] <= 10
    assert sum(timeout for _, timeout in calls) <= subject.DIAGNOSTIC_BUDGET_SECONDS


def test_failed_layer_skips_dependent_network_checks(monkeypatch, marker_path):
    monkeypatch.setattr(
        subject,
        "_dns_probe",
        lambda timeout, monotonic: (
            subject.ProbeResult("DNS", "ERROR", 1, "DNS_FAILURE"),
            [],
        ),
    )
    monkeypatch.setattr(
        subject,
        "_pynspd_probe",
        lambda monotonic: subject.ProbeResult("PYNSPD", "OK", 1),
    )
    output = io.StringIO()
    subject.run_diagnostics(logger=_logger(output), marker_path=marker_path)
    text = output.getvalue()
    assert "TCP status=SKIPPED reason=dns_unavailable" in text
    assert "probable_failure_layer=DNS" in text


def test_guard_prevents_network_requests_after_first_run(monkeypatch, marker_path):
    marker_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        subject,
        "_dns_probe",
        lambda *args: pytest.fail("network must not be called after completed run"),
    )
    output = io.StringIO()
    assert subject.run_diagnostics(logger=_logger(output), marker_path=marker_path) == 0
    assert "reason=already_completed" in output.getvalue()


def test_secret_values_are_redacted_from_diagnostic_logs(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "super-secret-token")
    logger = subject.configure_diagnostic_logging()
    logger.info("diagnostic detail=%s", "super-secret-token")
    captured = capsys.readouterr()
    assert "super-secret-token" not in captured.err
    assert "[REDACTED]" in captured.err


def test_pynspd_probe_is_offline_and_reports_effective_worker_settings(monkeypatch):
    monkeypatch.setenv("NSPD_CLIENT_TIMEOUT", "7")
    monkeypatch.setenv("PYNSPD_CLIENT_RETRIES", "99")
    result = subject._pynspd_probe(subject.time.monotonic)
    assert result.status == "OK"
    assert result.details["version"] == "1.1.15"
    assert result.details["timeout"] == "7"
    assert result.details["retries"] == 0
    assert result.details["tls_verify"] == "required"
    assert result.details["pynspd_env"] == "ignored"
