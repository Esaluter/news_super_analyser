from __future__ import annotations

import hashlib
import ssl
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CERTIFICATES = ROOT / "certificates"


def _der_sha256(path: Path) -> str:
    pem = path.read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest().upper()


def test_official_ministry_certificates_have_pinned_fingerprints():
    assert _der_sha256(CERTIFICATES / "russian_trusted_root_ca_2022.crt") == (
        "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"
    )
    assert _der_sha256(CERTIFICATES / "russian_trusted_sub_ca_2024.crt") == (
        "2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2"
    )


def test_official_root_can_be_loaded_as_a_verification_anchor():
    context = ssl.create_default_context(
        cafile=str(CERTIFICATES / "russian_trusted_root_ca_2022.crt")
    )
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
