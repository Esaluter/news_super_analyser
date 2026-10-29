from __future__ import annotations

import ssl

from news_super_analyser import gigachat_tls_check


def isolate_environment(monkeypatch):
    monkeypatch.setattr(gigachat_tls_check, "load_project_environment", lambda: None)
    monkeypatch.delenv("GIGACHAT_CA_BUNDLE", raising=False)


def test_default_tls_check_does_not_use_network(monkeypatch, capsys):
    isolate_environment(monkeypatch)
    monkeypatch.setattr(
        gigachat_tls_check,
        "_network_check",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network used")),
    )
    assert gigachat_tls_check.main([]) == 0
    output = capsys.readouterr().out
    assert "TLS verification: enabled" in output
    assert "Network was not used" in output


def test_explicit_network_mode_checks_both_public_endpoints(monkeypatch, capsys):
    isolate_environment(monkeypatch)
    calls = []

    def check(host, port, context):
        calls.append((host, port))
        return "OK dns_addresses=1 tls=TLSv1.3 cipher=TEST"

    monkeypatch.setattr(gigachat_tls_check, "_network_check", check)
    assert gigachat_tls_check.main(["--network"]) == 0
    assert calls == [
        ("ngw.devices.sberbank.ru", 9443),
        ("api.giga.chat", 443),
    ]
    assert "OK dns_addresses=1" in capsys.readouterr().out


def test_tls_verification_failure_reports_code_and_reason(monkeypatch, capsys):
    isolate_environment(monkeypatch)
    failure = ssl.SSLCertVerificationError(
        1, "certificate verify failed: self-signed certificate in certificate chain"
    )
    failure.verify_code = 19
    failure.verify_message = "self-signed certificate in certificate chain"
    monkeypatch.setattr(
        gigachat_tls_check,
        "_network_check",
        lambda *_args: (_ for _ in ()).throw(failure),
    )
    assert gigachat_tls_check.main(["--network"]) == 1
    output = capsys.readouterr().out
    assert "verify_code=19" in output
    assert "self-signed certificate in certificate chain" in output
