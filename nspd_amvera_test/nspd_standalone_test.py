#!/usr/bin/env python3
"""Standalone NSPD connectivity and public-address search diagnostic.

The script intentionally uses only the Python standard library. It does not
import the news_super_analyser package and does not use project configuration.
"""

from __future__ import annotations

import importlib.metadata
import hashlib
import html
import json
import os
import platform
import queue
import re
import socket
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable


HOST = "nspd.gov.ru"
PORT = 443
ROOT_URL = "https://nspd.gov.ru/"
API_URL = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
THEMATIC_SEARCH_ID = "1"
TCP_TIMEOUT_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 10.0
TOTAL_BUDGET_SECONDS = 180.0
MAX_RESPONSE_BYTES = 256_000
MAX_CONSOLE_CANDIDATES_PER_ADDRESS = 20
MAX_403_HEADER_VALUE_CHARS = 300
MAX_403_BODY_PREVIEW_CHARS = 500
HTTP_403_HEADER_NAMES = (
    "Server",
    "Via",
    "WWW-Authenticate",
    "Location",
    "Content-Type",
    "X-Request-ID",
    "X-Correlation-ID",
    "X-Cache",
    "CF-Ray",
)

# Official certificates published by the Russian Ministry of Digital
# Development via https://gu-st.ru/content/lending/. Their DER SHA-256
# fingerprints are verified before they are added to the trust store.
RUSSIAN_TRUSTED_CA_BUNDLE = """-----BEGIN CERTIFICATE-----
MIIFwjCCA6qgAwIBAgICEAAwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjIwMzAxMjEwNDE1WhcNMzIwMjI3MjEwNDE1WjBwMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMSAwHgYDVQQDDBdSdXNzaWFuIFRydXN0ZWQgUm9v
dCBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAMfFOZ8pUAL3+r2n
qqE0Zp52selXsKGFYoG0GM5bwz1bSFtCt+AZQMhkWQheI3poZAToYJu69pHLKS6Q
XBiwBC1cvzYmUYKMYZC7jE5YhEU2bSL0mX7NaMxMDmH2/NwuOVRj8OImVa5s1F4U
zn4Kv3PFlDBjjSjXKVY9kmjUBsXQrIHeaqmUIsPIlNWUnimXS0I0abExqkbdrXbX
YwCOXhOO2pDUx3ckmJlCMUGacUTnylyQW2VsJIyIGA8V0xzdaeUXg0VZ6ZmNUr5Y
Ber/EAOLPb8NYpsAhJe2mXjMB/J9HNsoFMBFJ0lLOT/+dQvjbdRZoOT8eqJpWnVD
U+QL/qEZnz57N88OWM3rabJkRNdU/Z7x5SFIM9FrqtN8xewsiBWBI0K6XFuOBOTD
4V08o4TzJ8+Ccq5XlCUW2L48pZNCYuBDfBh7FxkB7qDgGDiaftEkZZfApRg2E+M9
G8wkNKTPLDc4wH0FDTijhgxR3Y4PiS1HL2Zhw7bD3CbslmEGgfnnZojNkJtcLeBH
BLa52/dSwNU4WWLubaYSiAmA9IUMX1/RpfpxOxd4Ykmhz97oFbUaDJFipIggx5sX
ePAlkTdWnv+RWBxlJwMQ25oEHmRguNYf4Zr/Rxr9cS93Y+mdXIZaBEE0KS2iLRqa
OiWBki9IMQU4phqPOBAaG7A+eP8PAgMBAAGjZjBkMB0GA1UdDgQWBBTh0YHlzlpf
BKrS6badZrHF+qwshzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qwshzAS
BgNVHRMBAf8ECDAGAQH/AgEEMA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsF
AAOCAgEAALIY1wkilt/urfEVM5vKzr6utOeDWCUczmWX/RX4ljpRdgF+5fAIS4vH
tmXkqpSCOVeWUrJV9QvZn6L227ZwuE15cWi8DCDal3Ue90WgAJJZMfTshN4OI8cq
W9E4EG9wglbEtMnObHlms8F3CHmrw3k6KmUkWGoa+/ENmcVl68u/cMRl1JbW2bM+
/3A+SAg2c6iPDlehczKx2oa95QW0SkPPWGuNA/CE8CpyANIhu9XFrj3RQ3EqeRcS
AQQod1RNuHpfETLU/A2gMmvn/w/sx7TB3W5BPs6rprOA37tutPq9u6FTZOcG1Oqj
C/B7yTqgI7rbyvox7DEXoX7rIiEqyNNUguTk/u3SZ4VXE2kmxdmSh3TQvybfbnXV
4JbCZVaqiZraqc7oZMnRoWrXRG3ztbnbes/9qhRGI7PqXqeKJBztxRTEVj8ONs1d
WN5szTwaPIvhkhO3CO5ErU2rVdUr89wKpNXbBODFKRtgxUT70YpmJ46VVaqdAhOZ
D9EUUn4YaeLaS8AjSF/h7UkjOibNc4qVDiPP+rkehFWM66PVnP1Msh93tc+taIfC
EYVMxjh8zNbFuoc7fzvvrFILLe7ifvEIUqSVIC/AzplM/Jxw7buXFeGP1qVCBEHq
391d/9RAfaZ12zkwFsl+IKwE/OZxW8AHa9i1p4GO0YSNuczzEm4=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIG6DCCBNCgAwIBAgICEAUwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjQwNzE1MTI1MDQxWhcNMjkwNzE5MTI1MDQxWjBvMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMR8wHQYDVQQDDBZSdXNzaWFuIFRydXN0ZWQgU3Vi
IENBMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA1j0rkZECOt1S8o7I
JY+4YKAxuEa5xaHKHXT2EpkuC/0krqMOjUy2oPIRNgR5g8X0Jl6jamxeGLc4Q1tf
ju6or9oSRYThIUhRsFDQNBiBBEXoBgWxTfiKB2eyT97+pz5TBtBiRCPaLGRHYLRb
9Jz2HkJlxbtNPjtDrF5DPHym+mZ1M1z3hIQYAqJwLpsEBnsw/VxWMlxqHoeewd0h
uJMd71KQ5vOKlz7KrIZ6EobNNa6wItuvsfj3kYCK7O78uLHGXXFxdr8Hae9lMUmC
8F7AFwa+bO1LRlTlqW7rE3rLf+jj70N01N8T3o22v14YBaFBWQWncAVYD2JuL3tH
252+kdNOERf1fLbLRigJAbd+hOhWYlNf963TFDgnNPliHNIW72SygVBnI2V3JwO1
dp1hVKpK/zt8ziGdHW4gmOLTsH50YKdR4jNqUgQv4wASlKn9OpN6zHYc5G8h86fY
BM+zxE5ikGI+I/vIqBuI0eaDU92AWN/YjFLpu8tMu9kLRSCf1vug6FIfDPWVo7iP
ac/SI2v8jnnpaW7ph/Pz3WkzaG7ZZJsfFs+8dploWc6LOoDtbFBhMdGMxu024msC
0PSjZb5ODXPIaO2NsA7fMiAtZcoK6anTUJh4zOP/stA9qsJGNxdrEmiPXSmBZY/N
Y0wkZgZ6JTDhw7038bPvctkblJkCAwEAAaOCAYswggGHMB0GA1UdDgQWBBR3Pdk5
r0K93FvKduru/c4+YSkwXzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qws
hzAOBgNVHQ8BAf8EBAMCAYYwEgYDVR0TAQH/BAgwBgEB/wIBADCBmAYIKwYBBQUH
AQEEgYswgYgwQAYIKwYBBQUHMAKGNGh0dHA6Ly9udWMtY2RwLnZvc2tob2QucnUv
Y2RwL3Jvb3RjYV9zc2xfcnNhMjAyMi5jcnQwRAYIKwYBBQUHMAKGOGh0dHA6Ly9u
dWMtY2RwLmRpZ2l0YWwuZ292LnJ1L2NkcC9yb290Y2Ffc3NsX3JzYTIwMjIuY3J0
MIGFBgNVHR8EfjB8MDqgOKA2hjRodHRwOi8vbnVjLWNkcC52b3NraG9kLnJ1L2Nk
cC9yb290Y2Ffc3NsX3JzYTIwMjIuY3JsMD6gPKA6hjhodHRwOi8vbnVjLWNkcC5k
aWdpdGFsLmdvdi5ydS9jZHAvcm9vdGNhX3NzbF9yc2EyMDIyLmNybDANBgkqhkiG
9w0BAQsFAAOCAgEAmsINXtQ7wwUWvIeOr80MdJS/5G4xhyZOVEmeUorThquT672y
cCg3XCxc4fwbiZqSSbBqntQ7RtiTAKMYMvBageKoVHbzz+R4jX01tKcTx8cDePrz
dJ73bLNUorE7RU9QsW4KyiUeRmjMDV23AUlEvuQFTwgkHXvbac1BBdPn9CrssQuF
5EGohZKcQPFiAAc4SHbRNhlr7uAwgpc/erzI9EAcvA6BVAXcVKoeGpV01uexUgZ6
St5RP9UmDWNA7T4yVXWJ233N0Q8bl+6AswINQ3PosPu6yQQHQjr65YS06epK+AeI
6j+oGR4xI7EhTQhQvaobnGmX/8QQ7XDRYCP2HXYxiffnn/CfZ/BVyKLYeY1ZipjE
nzqdQIC2+Q3WtY8jsVRQMP38WFRmtsIt5snehnPTs5bKGVIcYzj3o3Ex/K7agEz0
zAJ0JR5ivXZOvNkT0g9x1v+S1IkU3e/nX1a+tpRquMtnHX0L2lXArNHUbaOO9EJt
d57WaIpofV5cVhhwShOgAuBc9UMJF3/n4t4RKiPxtsK8P67gcmphMhslj7AMYrYM
ej2NvQZY4m3ub3CPC/PrTjDONvb+8g5xrKtxBjYqC74HSB4dg9G3WimSDUuP2Su6
G2y2TUeyJuCvCLz289VoO0vg7cNdMobE3KCqAiiNhN2VBFxHAUKmUoRcRdw=
-----END CERTIFICATE-----
"""

EXPECTED_CA_FINGERPRINTS = {
    "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",
    "2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2",
}

TEST_ADDRESSES = (
    {"region": "Волгоградская область", "address": "Волгоград, ул. Ополченская, 11А"},
    {"region": "Москва", "address": "Москва, Профсоюзная ул., 123А, стр. 15"},
    {"region": "Белгородская область", "address": "Шебекино, ул. Тимирязева, 62"},
    {"region": "Тульская область", "address": "Новомосковск, ул. Водопьянова, 1"},
    {"region": "Тульская область", "address": "Новомосковск, ул. Водопьянова, 1А"},
    {"region": "Тульская область", "address": "Новомосковск, ул. Связи, 10"},
    {"region": "Курская область", "address": "Льгов, ул. Карла Маркса, 47"},
    {"region": "Курская область", "address": "Малое Городьково, Конышевский район"},
    {"region": "Московская область", "address": "Котельники, СНТ «Горняк», 136"},
    {"region": "ЛНР", "address": "Луганск, ул. Почтовая, 22"},
)


class DiagnosticDeadline(TimeoutError):
    """The global standalone diagnostic deadline expired."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class TextOnlyHTMLParser(HTMLParser):
    """Collect visible HTML text while ignoring executable and styling blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class Reporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str = "") -> None:
        print(message, flush=True)
        self.lines.append(message)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def duration_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def remaining_timeout(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiagnosticDeadline("global diagnostic deadline exceeded")
    return max(0.001, min(maximum, remaining))


def bounded_call(function: Callable[[], Any], timeout: float) -> Any:
    """Give DNS resolution a hard deadline on Windows and Linux."""

    output: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            output.put((True, function()))
        except BaseException as exc:
            output.put((False, exc))

    thread = threading.Thread(target=target, daemon=True, name="nspd-standalone-dns")
    thread.start()
    try:
        ok, value = output.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("operation deadline exceeded") from exc
    if ok:
        return value
    raise value


def embedded_ca_fingerprints() -> list[str]:
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        RUSSIAN_TRUSTED_CA_BUNDLE,
        flags=re.DOTALL,
    )
    fingerprints = [
        hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        for certificate in certificates
    ]
    if set(fingerprints) != EXPECTED_CA_FINGERPRINTS:
        raise RuntimeError("embedded CA fingerprint verification failed")
    return fingerprints


def build_ssl_context(reporter: Reporter) -> tuple[ssl.SSLContext, dict[str, Any]]:
    """Build one verified context shared by TLS, HTTP, and API checks."""

    defaults = ssl.get_default_verify_paths()
    context = ssl.create_default_context()
    info: dict[str, Any] = {
        "system_cafile": defaults.cafile,
        "system_capath": defaults.capath,
        "openssl_cafile_env": defaults.openssl_cafile_env,
        "openssl_capath_env": defaults.openssl_capath_env,
        "certifi_path": None,
        "certifi_version": None,
        "certifi_error": None,
    }

    try:
        import certifi  # type: ignore[import-not-found]

        certifi_path = certifi.where()
        context.load_verify_locations(cafile=certifi_path)
        info["certifi_path"] = certifi_path
        info["certifi_version"] = importlib.metadata.version("certifi")
    except importlib.metadata.PackageNotFoundError:
        info["certifi_error"] = "not installed"
    except (ImportError, OSError, ssl.SSLError) as exc:
        info["certifi_error"] = safe_error(exc)

    fingerprints = embedded_ca_fingerprints()
    context.load_verify_locations(cadata=RUSSIAN_TRUSTED_CA_BUNDLE)
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context is unavailable")

    info.update(
        {
            "embedded_ca_source": "official gu-st.ru certificates embedded in script",
            "embedded_ca_fingerprints_sha256": fingerprints,
            "verify_mode": "CERT_REQUIRED",
            "check_hostname": True,
            "server_hostname": HOST,
            "ca_bundle_description": (
                f"system_cafile={defaults.cafile or 'platform-default'}; "
                f"system_capath={defaults.capath or 'platform-default'}; "
                f"certifi={info['certifi_path'] or info['certifi_error'] or 'unavailable'}; "
                "embedded=Russian Trusted Root CA 2022 + Sub CA 2024"
            ),
        }
    )
    reporter.log(
        f"[TLS-CA] system_cafile={defaults.cafile or 'platform-default'} | "
        f"system_capath={defaults.capath or 'platform-default'}"
    )
    reporter.log(
        f"[TLS-CA] certifi={info['certifi_path'] or info['certifi_error'] or 'unavailable'}"
    )
    reporter.log(
        "[TLS-CA] embedded=Russian Trusted Root CA 2022 + Sub CA 2024 | "
        f"sha256={','.join(fingerprints)}"
    )
    reporter.log(
        f"[TLS-CA] verify_mode=CERT_REQUIRED | check_hostname=True | SNI={HOST}"
    )
    return context, info


def safe_error(exc: BaseException) -> dict[str, Any]:
    """Return useful error classes without URLs, credentials, or response bodies."""

    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        category = "TLS_CERTIFICATE_VERIFICATION_FAILURE"
    elif isinstance(reason, ssl.SSLError):
        category = "TLS_HANDSHAKE_FAILURE"
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        category = "TIMEOUT"
    elif isinstance(reason, ConnectionRefusedError):
        category = "CONNECTION_REFUSED"
    elif isinstance(reason, socket.gaierror):
        category = "DNS_FAILURE"
    else:
        category = type(reason).__name__
    result: dict[str, Any] = {
        "type": type(reason).__name__,
        "category": category,
        "message": " ".join(str(reason).split())[:500],
    }
    if isinstance(reason, ssl.SSLCertVerificationError):
        result["verify_code"] = getattr(reason, "verify_code", None)
        result["verify_message"] = getattr(reason, "verify_message", None)
    return result


def error_log_details(error: dict[str, Any]) -> str:
    fields = [str(error.get("category", "UNKNOWN"))]
    if error.get("verify_code") is not None:
        fields.append(f"verify_code={error['verify_code']}")
    if error.get("verify_message"):
        fields.append(f"verify_message={error['verify_message']}")
    if error.get("message"):
        fields.append(f"message={error['message']}")
    return " | ".join(fields)


def environment_diagnostic(reporter: Reporter) -> dict[str, Any]:
    try:
        pynspd_version = importlib.metadata.version("pynspd")
    except importlib.metadata.PackageNotFoundError:
        pynspd_version = None
    proxy_names = sorted(
        name
        for name in os.environ
        if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    )
    result = {
        "utc": now_utc(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "operating_system": platform.platform(),
        "hostname": socket.gethostname(),
        "pynspd_version": pynspd_version,
        "http_client": f"urllib.request (Python {platform.python_version()} stdlib)",
        "proxy_environment_present": bool(proxy_names),
        "proxy_environment_names": proxy_names,
        "proxy_usage": "disabled_for_this_script",
    }
    reporter.log(
        f"[ENV] Python {result['python']} | {platform.system()} | "
        f"pynspd={pynspd_version or 'not installed'}"
    )
    reporter.log(
        "[ENV] Proxy variables: "
        + (", ".join(proxy_names) if proxy_names else "none")
        + " | values not displayed | proxy use disabled"
    )
    return result


def dns_diagnostic(
    reporter: Reporter, deadline: float
) -> tuple[dict[str, Any], list[tuple[int, tuple[Any, ...], str]]]:
    started = time.monotonic()
    try:
        timeout = remaining_timeout(deadline, HTTP_TIMEOUT_SECONDS)
        raw = bounded_call(
            lambda: socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM), timeout
        )
        records: list[tuple[int, tuple[Any, ...], str]] = []
        seen: set[tuple[int, str]] = set()
        for family, _socktype, _proto, _canonname, sockaddr in raw:
            address = str(sockaddr[0])
            key = (family, address)
            if family not in {socket.AF_INET, socket.AF_INET6} or key in seen:
                continue
            seen.add(key)
            records.append((family, sockaddr, address))
        ipv4 = [item[2] for item in records if item[0] == socket.AF_INET]
        ipv6 = [item[2] for item in records if item[0] == socket.AF_INET6]
        if not records:
            raise socket.gaierror("no IPv4 or IPv6 addresses")
        result = {
            "status": "OK",
            "duration_ms": duration_ms(started),
            "ipv4": ipv4,
            "ipv6": ipv6,
        }
        reporter.log(
            f"[DNS] OK | IPv4={len(ipv4)} IPv6={len(ipv6)} | "
            f"{result['duration_ms']} ms"
        )
        for address in ipv4:
            reporter.log(f"[DNS] IPv4 {address}")
        for address in ipv6:
            reporter.log(f"[DNS] IPv6 {address}")
        return result, records
    except Exception as exc:
        result = {
            "status": "ERROR",
            "duration_ms": duration_ms(started),
            "error": safe_error(exc),
            "ipv4": [],
            "ipv6": [],
        }
        reporter.log(
            f"[DNS] ERROR | {result['error']['category']} | {result['duration_ms']} ms"
        )
        return result, []


def connect_socket(
    family: int, sockaddr: tuple[Any, ...], timeout: float
) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        return sock
    except BaseException:
        sock.close()
        raise


def tcp_diagnostic(
    reporter: Reporter,
    deadline: float,
    records: list[tuple[int, tuple[Any, ...], str]],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for family, sockaddr, address in records:
        started = time.monotonic()
        family_name = "IPv4" if family == socket.AF_INET else "IPv6"
        try:
            timeout = remaining_timeout(deadline, TCP_TIMEOUT_SECONDS)
            sock = connect_socket(family, sockaddr, timeout)
            sock.close()
            item = {
                "family": family_name,
                "address": address,
                "status": "OK",
                "duration_ms": duration_ms(started),
            }
            reporter.log(
                f"[TCP] OK | {family_name} {address}:{PORT} | {item['duration_ms']} ms"
            )
        except Exception as exc:
            item = {
                "family": family_name,
                "address": address,
                "status": "ERROR",
                "duration_ms": duration_ms(started),
                "error": safe_error(exc),
            }
            reporter.log(
                f"[TCP] ERROR | {family_name} {address}:{PORT} | "
                f"{item['error']['category']} | {item['duration_ms']} ms"
            )
        attempts.append(item)
        if time.monotonic() >= deadline:
            break
    return {
        "status": "OK" if any(item["status"] == "OK" for item in attempts) else "ERROR",
        "attempts": attempts,
    }


def tls_diagnostic(
    reporter: Reporter,
    deadline: float,
    records: list[tuple[int, tuple[Any, ...], str]],
    tcp_result: dict[str, Any],
    context: ssl.SSLContext,
    ca_info: dict[str, Any],
) -> dict[str, Any]:
    successful = {
        (item["family"], item["address"])
        for item in tcp_result["attempts"]
        if item["status"] == "OK"
    }
    attempts: list[dict[str, Any]] = []
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        result = {
            "status": "ERROR",
            "attempts": [],
            "error": {"type": "RuntimeError", "category": "TLS_VERIFICATION_UNAVAILABLE"},
        }
        reporter.log("[TLS] ERROR | TLS_VERIFICATION_UNAVAILABLE")
        return result
    for family, sockaddr, address in records:
        family_name = "IPv4" if family == socket.AF_INET else "IPv6"
        if (family_name, address) not in successful:
            continue
        started = time.monotonic()
        raw_socket: socket.socket | None = None
        try:
            timeout = remaining_timeout(deadline, HTTP_TIMEOUT_SECONDS)
            raw_socket = connect_socket(family, sockaddr, timeout)
            raw_socket.settimeout(
                max(0.001, min(timeout, deadline - time.monotonic()))
            )
            with context.wrap_socket(raw_socket, server_hostname=HOST) as tls_socket:
                raw_socket = None
                cipher = tls_socket.cipher()
                item = {
                    "family": family_name,
                    "address": address,
                    "status": "OK",
                    "tls_version": tls_socket.version(),
                    "cipher_suite": cipher[0] if cipher else None,
                    "duration_ms": duration_ms(started),
                }
            reporter.log(
                f"[TLS] OK | {family_name} {address} | {item['tls_version']} | "
                f"{item['cipher_suite']} | {item['duration_ms']} ms"
            )
            attempts.append(item)
            break
        except Exception as exc:
            error = safe_error(exc)
            error["ca_bundle"] = ca_info["ca_bundle_description"]
            item = {
                "family": family_name,
                "address": address,
                "status": "ERROR",
                "duration_ms": duration_ms(started),
                "error": error,
            }
            reporter.log(
                f"[TLS] ERROR | {family_name} {address} | "
                f"{error_log_details(error)} | {item['duration_ms']} ms | "
                f"ca_bundle={ca_info['ca_bundle_description']}"
            )
            attempts.append(item)
        finally:
            if raw_socket is not None:
                raw_socket.close()
    return {
        "status": "OK" if any(item["status"] == "OK" for item in attempts) else "ERROR",
        "attempts": attempts,
    }


def request_headers() -> dict[str, str]:
    """Headers used by the verified pynspd 1.1.15 implementation."""

    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Referer": "https://nspd.gov.ru/map?thematic=PKK",
        "Host": HOST,
        "Accept": "application/json, text/plain, */*",
    }


def sanitize_diagnostic_text(value: Any, limit: int) -> str:
    """Make untrusted response text single-line, bounded, and safe to log."""

    text = html.unescape(str(value or ""))
    text = "".join(
        " " if character.isspace() or unicodedata.category(character).startswith("C") else character
        for character in text
    )
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+",
        r"\1 [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(access[_-]?token|refresh[_-]?token|token|secret|password|passwd|"
        r"session(?:[_-]?id)?|sid|cookie|api[_-]?key|authorization|auth)"
        r"\b(\s*[:=]\s*)([^\s&;,]+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(
        r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}(?:\.[a-zA-Z0-9_-]{8,})?\b",
        "[REDACTED-JWT]",
        text,
    )
    text = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED-EMAIL]",
        text,
    )
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def decode_http_body(body: bytes, content_type: str | None) -> str:
    charset_match = re.search(r"(?i)\bcharset\s*=\s*['\"]?([^\s;'\"]+)", content_type or "")
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def html_text_preview(body: bytes, content_type: str | None) -> str:
    parser = TextOnlyHTMLParser()
    try:
        parser.feed(decode_http_body(body, content_type))
        parser.close()
        visible_text = " ".join(parser.parts)
    except Exception:
        visible_text = ""
    return sanitize_diagnostic_text(visible_text, MAX_403_BODY_PREVIEW_CHARS)


def build_http_403_diagnostic(
    status: int,
    headers: Any,
    body: bytes,
) -> dict[str, Any] | None:
    if status != 403:
        return None
    safe_headers = {
        name: sanitize_diagnostic_text(headers.get(name), MAX_403_HEADER_VALUE_CHARS)
        for name in HTTP_403_HEADER_NAMES
        if headers.get(name) is not None
    }
    content_type = str(headers.get("Content-Type") or "")
    preview = html_text_preview(body, content_type) if "html" in content_type.lower() else ""
    return {
        "headers": safe_headers,
        "body_preview": preview or None,
        "body_preview_chars": len(preview),
    }


def log_http_403_diagnostic(
    reporter: Reporter, label: str, result: dict[str, Any]
) -> None:
    diagnostic = result.get("http_403")
    if not isinstance(diagnostic, dict):
        return
    headers = diagnostic.get("headers", {})
    for name in HTTP_403_HEADER_NAMES:
        if name in headers:
            reporter.log(f"[HTTP-403 {label}] {name.lower()}={headers[name]}")
    if diagnostic.get("body_preview"):
        reporter.log(f"[HTTP-403 {label}] body-preview={diagnostic['body_preview']}")


def http_get(
    url: str,
    timeout: float,
    context: ssl.SSLContext,
    ca_info: dict[str, Any],
) -> dict[str, Any]:
    """Perform one direct, TLS-verified GET without proxy, redirect, or retry."""

    started = time.monotonic()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context is unavailable")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        NoRedirect(),
    )
    request = urllib.request.Request(url, headers=request_headers(), method="GET")
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(response.status if hasattr(response, "status") else response.code)
            headers = response.headers
        limited_body = body[:MAX_RESPONSE_BYTES]
        forbidden_diagnostic = build_http_403_diagnostic(status, headers, limited_body)
        safe_content_type = sanitize_diagnostic_text(
            headers.get("Content-Type"), MAX_403_HEADER_VALUE_CHARS
        )
        return {
            "transport_ok": True,
            "status": status,
            "duration_ms": duration_ms(started),
            "content_type": safe_content_type or None,
            "headers": {
                name: sanitize_diagnostic_text(headers.get(name), MAX_403_HEADER_VALUE_CHARS)
                for name in ("Content-Type", "Content-Length", "Date", "Server", "Location", "Via")
                if headers.get(name) is not None
            },
            "http_403": forbidden_diagnostic,
            "body": limited_body,
            "body_truncated": len(body) > MAX_RESPONSE_BYTES,
        }
    except Exception as exc:
        error = safe_error(exc)
        error["ca_bundle"] = ca_info["ca_bundle_description"]
        return {
            "transport_ok": False,
            "status": None,
            "duration_ms": duration_ms(started),
            "error": error,
            "body": b"",
            "body_truncated": False,
        }


def public_http_result(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "body"}


def root_http_diagnostic(
    reporter: Reporter,
    deadline: float,
    context: ssl.SSLContext,
    ca_info: dict[str, Any],
) -> dict[str, Any]:
    try:
        raw = http_get(
            ROOT_URL,
            remaining_timeout(deadline, HTTP_TIMEOUT_SECONDS),
            context,
            ca_info,
        )
    except Exception as exc:
        raw = {
            "transport_ok": False,
            "status": None,
            "duration_ms": 0,
            "error": safe_error(exc),
            "body": b"",
        }
    result = public_http_result(raw)
    if raw["transport_ok"]:
        result["status_label"] = "OK" if int(raw["status"]) < 400 else "ERROR"
        reporter.log(
            f"[HTTP] {raw['status']} | {raw['duration_ms']} ms | "
            f"content-type={raw.get('content_type') or 'unknown'}"
        )
        log_http_403_diagnostic(reporter, "ROOT", result)
    else:
        result["status_label"] = "ERROR"
        reporter.log(
            f"[HTTP] ERROR | {error_log_details(raw['error'])} | "
            f"{raw['duration_ms']} ms | "
            f"ca_bundle={raw['error'].get('ca_bundle', ca_info['ca_bundle_description'])}"
        )
    return result


def normalized_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[.,«»\"']", " ", text)
    text = re.sub(r"[^0-9a-zа-я/]+", " ", text)
    return " ".join(text.split())


def region_key(value: str) -> str:
    ignored = {
        "область", "обл", "край", "республика", "респ", "автономный",
        "автономная", "округ", "ао", "город", "г",
    }
    return " ".join(word for word in normalized_text(value).split() if word not in ignored)


def locality_key(value: str) -> str:
    first = str(value or "").split(",", 1)[0]
    words = normalized_text(first).split()
    prefixes = {"г", "город", "с", "село", "п", "пос", "поселок", "деревня", "д"}
    while words and words[0] in prefixes:
        words.pop(0)
    return " ".join(words)


def street_key(value: str) -> str:
    marker = (
        r"(?:улица|ул|проспект|просп|пр\s+т|переулок|пер|шоссе|ш|"
        r"бульвар|бул|б\s+р|проезд|пр\s+д|набережная|наб|площадь|пл|"
        r"аллея|тупик|линия)"
    )
    for segment in re.split(r"[,;]", str(value or "")):
        text = normalized_text(segment)
        prefix = re.search(rf"\b{marker}\s+(.+)$", text)
        if prefix:
            return re.split(
                r"\s+(?:д|дом|стр|строение|корп|корпус)\b", prefix.group(1), maxsplit=1
            )[0].strip()
        suffix = re.search(rf"^(.+?)\s+{marker}\b", text)
        if suffix:
            return suffix.group(1).strip()
    return ""


def house_key(value: str) -> str:
    text = normalized_text(value)
    match = re.search(r"\b(?:д|дом)\s*(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)", text)
    if not match:
        match = re.search(r"\b(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)$", text)
    return match.group(1) if match else ""


def qualifier_key(value: str, names: str) -> str:
    match = re.search(rf"\b(?:{names})\s*(\d+[а-яa-z]?)\b", normalized_text(value))
    return match.group(1) if match else ""


def normalize_cadastral_number(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2}):(\d{1,7}):(\d+)", text)
    return ":".join(match.groups()) if match else ""


def dictionary_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    normalized_names = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in names}
    for key, value in data.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if normalized_key in normalized_names and value not in (None, ""):
            return value
    return None


def extract_candidates(features: list[Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            continue
        options = properties.get("options")
        if not isinstance(options, dict):
            continue
        number = normalize_cadastral_number(
            dictionary_value(options, ("cad_number", "cad_num", "cadNumber", "cadNum"))
        )
        if not number or number in seen:
            continue
        address = dictionary_value(
            options,
            (
                "readable_address",
                "address_readable_address",
                "readableAddress",
                "addressReadableAddress",
            ),
        )
        seen.add(number)
        candidates.append(
            {
                "cadastral_number": number,
                "address": str(address or "").strip(),
                "category": str(properties.get("category") or ""),
                "category_name": str(
                    properties.get("categoryName")
                    or properties.get("category_name")
                    or ""
                ),
            }
        )
    return candidates


def classify_candidate(source: dict[str, str], candidate: dict[str, str]) -> str:
    candidate_address = candidate.get("address", "")
    normalized_candidate = normalized_text(candidate_address)
    if not candidate_address:
        return "AMBIGUOUS"
    source_region = region_key(source["region"])
    source_locality = locality_key(source["address"])
    source_street = street_key(source["address"])
    source_house = house_key(source["address"])
    source_corpus = qualifier_key(source["address"], "корп|корпус")
    source_building = qualifier_key(source["address"], "стр|строение")
    candidate_street = street_key(candidate_address)
    candidate_house = house_key(candidate_address)

    region_matches = not source_region or source_region in normalized_candidate
    locality_matches = not source_locality or source_locality in normalized_candidate
    street_matches = bool(source_street and candidate_street == source_street)
    house_matches = bool(source_house and candidate_house == source_house)
    qualifiers_match = True
    for source_value, names in (
        (source_corpus, "корп|корпус"),
        (source_building, "стр|строение"),
    ):
        if source_value and qualifier_key(candidate_address, names) != source_value:
            qualifiers_match = False

    if (
        source_street
        and source_house
        and region_matches
        and locality_matches
        and street_matches
        and house_matches
        and qualifiers_match
    ):
        return "EXACT"
    if source_street and source_house and street_matches and house_matches:
        return "PARTIAL"
    if locality_matches and (street_matches or house_matches):
        return "PARTIAL"
    if not source_street and locality_matches:
        return "PARTIAL"
    return "MISMATCH"


def overall_match(candidates: list[dict[str, str]]) -> str:
    exact = sum(item["match"] == "EXACT" for item in candidates)
    partial = sum(item["match"] == "PARTIAL" for item in candidates)
    if exact == 1:
        return "EXACT"
    if exact > 1:
        return "AMBIGUOUS"
    if partial == 1:
        return "PARTIAL"
    if partial > 1:
        return "AMBIGUOUS"
    if candidates:
        return "MISMATCH"
    return "NO_RESULTS"


def parse_search_response(raw: dict[str, Any], source: dict[str, str]) -> dict[str, Any]:
    result = public_http_result(raw)
    result.update(
        {
            "method": "GET",
            "endpoint": API_URL,
            "json_parsed": False,
            "schema_valid": False,
            "results_count": 0,
            "candidates": [],
            "match": "TECHNICAL_ERROR",
            "technical_success": False,
        }
    )
    if not raw["transport_ok"]:
        return result
    try:
        payload = json.loads(raw["body"].decode("utf-8"))
        result["json_parsed"] = True
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["parse_error"] = safe_error(exc)
        return result
    # pynspd 1.1.15 treats HTTP 404 from this search endpoint as a valid empty
    # search result (its _search method catches NotFound and returns None).
    if raw["status"] == 404:
        result["schema_valid"] = True
        result["match"] = "NO_RESULTS"
        result["technical_success"] = True
        return result
    data = payload.get("data") if isinstance(payload, dict) else None
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        result["schema_error"] = "expected data.features list"
        return result
    result["schema_valid"] = True
    result["results_count"] = len(features)
    candidates = extract_candidates(features)
    for candidate in candidates:
        candidate["match"] = classify_candidate(source, candidate)
    result["candidates"] = candidates
    result["match"] = overall_match(candidates)
    result["technical_success"] = 200 <= int(raw["status"]) < 300
    return result


def is_transient(raw: dict[str, Any]) -> bool:
    status = raw.get("status")
    if isinstance(status, int) and status >= 500:
        return True
    if raw.get("transport_ok"):
        return False
    category = str(raw.get("error", {}).get("category", ""))
    return category in {"TIMEOUT", "ConnectionResetError", "RemoteDisconnected", "OSError"}


def search_address(
    reporter: Reporter,
    deadline: float,
    index: int,
    source: dict[str, str],
    context: ssl.SSLContext,
    ca_info: dict[str, Any],
) -> dict[str, Any]:
    query = ", ".join((source["region"], source["address"]))
    url = API_URL + "?" + urllib.parse.urlencode(
        {"query": query, "thematicSearchId": THEMATIC_SEARCH_ID}
    )
    reporter.log(f"[{index:02d}/{len(TEST_ADDRESSES):02d}] {source['address']}")
    raw: dict[str, Any] = {}
    attempts = 0
    for attempt in range(1, 3):
        attempts = attempt
        try:
            timeout = remaining_timeout(deadline, HTTP_TIMEOUT_SECONDS)
        except DiagnosticDeadline as exc:
            raw = {
                "transport_ok": False,
                "status": None,
                "duration_ms": 0,
                "error": safe_error(exc),
                "body": b"",
                "body_truncated": False,
            }
            break
        raw = http_get(url, timeout, context, ca_info)
        status = raw.get("status")
        if status in {403, 429} or not is_transient(raw) or attempt == 2:
            break
        reporter.log("  Временная сетевая ошибка; выполняется единственный повтор")
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    result = parse_search_response(raw, source)
    result.update(
        {
            "index": index,
            "region": source["region"],
            "address": source["address"],
            "query": query,
            "attempts": attempts,
        }
    )
    if raw.get("transport_ok"):
        reporter.log(f"  HTTP: {raw.get('status')} | {raw.get('duration_ms')} ms")
        log_http_403_diagnostic(reporter, "API", result)
        reporter.log(
            f"  JSON: {'OK' if result['json_parsed'] else 'ERROR'} | "
            f"schema: {'OK' if result['schema_valid'] else 'ERROR'}"
        )
    else:
        reporter.log(
            f"  ERROR: {error_log_details(raw.get('error', {}))} | "
            f"{raw.get('duration_ms', 0)} ms | "
            f"ca_bundle={raw.get('error', {}).get('ca_bundle', ca_info['ca_bundle_description'])}"
        )
    reporter.log(f"  Результатов API: {result['results_count']}")
    if result["candidates"]:
        for candidate in result["candidates"][:MAX_CONSOLE_CANDIDATES_PER_ADDRESS]:
            reporter.log(
                "  Кадастровый номер: "
                f"{candidate['cadastral_number']} | {candidate['match']} | "
                f"{candidate['category_name'] or candidate['category'] or 'тип неизвестен'}"
            )
            reporter.log(f"  Адрес НСПД: {candidate['address'] or 'не указан'}")
        omitted = len(result["candidates"]) - MAX_CONSOLE_CANDIDATES_PER_ADDRESS
        if omitted > 0:
            reporter.log(
                f"  ... ещё {omitted} кандидатов сохранены полностью в JSON"
            )
    else:
        reporter.log("  Кадастровые номера: не найдены")
    reporter.log(f"  Совпадение: {result['match']}")
    reporter.log()
    return result


def skipped(stage: str, reason: str) -> dict[str, str]:
    return {"status": "SKIPPED", "stage": stage, "reason": reason}


def compare_http_403_responses(report: dict[str, Any]) -> dict[str, Any]:
    root_result = report.get("http", {})
    searches = report.get("searches", [])
    api_result = searches[0] if searches else {}
    root_status = root_result.get("status")
    api_status = api_result.get("status")
    comparison: dict[str, Any] = {
        "root_status": root_status,
        "api_status": api_status,
        "both_forbidden": root_status == 403 and api_status == 403,
        "characteristic_headers_match": None,
        "matching_headers": [],
        "different_headers": [],
        "body_preview_match": None,
        "possible_source": "INCONCLUSIVE",
        "source_evidence": [],
        "ip_blocking_proven": False,
    }
    if not comparison["both_forbidden"]:
        return comparison

    root_diagnostic = root_result.get("http_403") or {}
    api_diagnostic = api_result.get("http_403") or {}
    root_headers = root_diagnostic.get("headers", {})
    api_headers = api_diagnostic.get("headers", {})
    stable_names = {
        "Server", "Via", "WWW-Authenticate", "Location", "Content-Type", "X-Cache"
    }
    common_names = sorted(stable_names & root_headers.keys() & api_headers.keys())
    matching = [name for name in common_names if root_headers[name] == api_headers[name]]
    different = [name for name in common_names if root_headers[name] != api_headers[name]]
    comparison["matching_headers"] = matching
    comparison["different_headers"] = different
    if common_names:
        comparison["characteristic_headers_match"] = not different

    root_preview = root_diagnostic.get("body_preview")
    api_preview = api_diagnostic.get("body_preview")
    if root_preview and api_preview:
        comparison["body_preview_match"] = root_preview == api_preview

    combined_headers = {**root_headers, **api_headers}
    server_values = " ".join(
        str(item.get("Server", "")) for item in (root_headers, api_headers)
    ).lower()
    if "CF-Ray" in combined_headers or "cloudflare" in server_values:
        comparison["possible_source"] = "CLOUDFLARE_EDGE"
        comparison["source_evidence"] = ["CF-Ray or Cloudflare Server header"]
    elif "WWW-Authenticate" in combined_headers:
        comparison["possible_source"] = "AUTHENTICATION_LAYER"
        comparison["source_evidence"] = ["WWW-Authenticate header"]
    elif "Via" in combined_headers or "X-Cache" in combined_headers:
        comparison["possible_source"] = "INTERMEDIARY_OR_CACHE"
        comparison["source_evidence"] = ["Via or X-Cache header"]
    return comparison


def log_http_403_summary(reporter: Reporter, comparison: dict[str, Any]) -> None:
    reporter.log(
        "[HTTP-403 SUMMARY] "
        f"root_status={comparison['root_status']} api_status={comparison['api_status']}"
    )
    if comparison["both_forbidden"]:
        reporter.log(
            "[HTTP-403 SUMMARY] "
            f"characteristic_headers_match={comparison['characteristic_headers_match']} "
            f"matching_headers={','.join(comparison['matching_headers']) or 'none'} "
            f"different_headers={','.join(comparison['different_headers']) or 'none'} "
            f"body_preview_match={comparison['body_preview_match']}"
        )
        reporter.log(
            "[HTTP-403 SUMMARY] "
            f"possible_source={comparison['possible_source']} "
            "ip_blocking_proven=false"
        )


def make_summary(report: dict[str, Any], elapsed: float) -> dict[str, Any]:
    searches = report.get("searches", [])
    technically_successful = sum(item.get("technical_success", False) for item in searches)
    results_found = sum(int(item.get("results_count", 0)) for item in searches)
    cadastral_numbers = {
        candidate["cadastral_number"]
        for item in searches
        for candidate in item.get("candidates", [])
        if candidate.get("cadastral_number")
    }
    matches = {name: sum(item.get("match") == name for item in searches) for name in ("EXACT", "PARTIAL", "AMBIGUOUS")}
    errors = sum(not item.get("technical_success", False) for item in searches)
    errors += sum(
        report.get(stage, {}).get("status") == "ERROR"
        for stage in ("dns", "tcp", "tls", "http")
    )

    if report.get("dns", {}).get("status") != "OK":
        conclusion = "DNS_FAILURE"
    elif report.get("tcp", {}).get("status") != "OK":
        conclusion = "TCP_UNAVAILABLE"
    elif report.get("tls", {}).get("status") != "OK":
        conclusion = "TLS_FAILURE"
    elif any(item.get("status") in {403, 429} for item in searches):
        conclusion = "API_REJECTED"
    elif any(item.get("json_parsed") and not item.get("schema_valid") for item in searches):
        conclusion = "API_SCHEMA_MISMATCH"
    elif not technically_successful:
        conclusion = "HTTP_FAILURE"
    elif results_found == 0:
        conclusion = "API_ACCESSIBLE_NO_RESULTS"
    elif cadastral_numbers and errors == 0:
        conclusion = "API_SEARCH_WORKING"
    else:
        conclusion = "PARTIAL_SUCCESS"

    return {
        "dns": report.get("dns", {}).get("status", "SKIPPED"),
        "tcp": report.get("tcp", {}).get("status", "SKIPPED"),
        "tls": report.get("tls", {}).get("status", "SKIPPED"),
        "http": report.get("http", {}).get("status_label", report.get("http", {}).get("status", "SKIPPED")),
        "api": "OK" if technically_successful else ("ERROR" if searches else "SKIPPED"),
        "addresses_checked": len(searches),
        "technically_successful_responses": technically_successful,
        "results_found": results_found,
        "unique_cadastral_numbers": len(cadastral_numbers),
        "exact_matches": matches["EXACT"],
        "partial_matches": matches["PARTIAL"],
        "ambiguous_matches": matches["AMBIGUOUS"],
        "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "conclusion": conclusion,
    }


def print_summary(reporter: Reporter, summary: dict[str, Any]) -> None:
    reporter.log("=== ИТОГОВАЯ СВОДКА ===")
    for label, key in (
        ("DNS", "dns"),
        ("TCP", "tcp"),
        ("TLS", "tls"),
        ("HTTP", "http"),
        ("API", "api"),
    ):
        reporter.log(f"{label}: {summary[key]}")
    reporter.log(f"Проверено адресов: {summary['addresses_checked']}")
    reporter.log(
        "Технически успешных ответов: "
        f"{summary['technically_successful_responses']}"
    )
    reporter.log(f"Найдено результатов API: {summary['results_found']}")
    reporter.log(
        f"Найдено уникальных кадастровых номеров: {summary['unique_cadastral_numbers']}"
    )
    reporter.log(
        "Совпадения: "
        f"EXACT={summary['exact_matches']} "
        f"PARTIAL={summary['partial_matches']} "
        f"AMBIGUOUS={summary['ambiguous_matches']}"
    )
    reporter.log(f"Ошибок: {summary['errors']}")
    reporter.log(f"Общее время: {summary['elapsed_seconds']} сек")
    reporter.log(f"Диагностическое заключение: {summary['conclusion']}")


def save_reports(
    reporter: Reporter, report: dict[str, Any], timestamp: str
) -> tuple[str | None, str | None]:
    output_directory = Path(__file__).resolve().parent
    json_path = output_directory / f"nspd_diagnostic_{timestamp}.json"
    txt_path = output_directory / f"nspd_diagnostic_{timestamp}.txt"
    try:
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        txt_path.write_text("\n".join(reporter.lines) + "\n", encoding="utf-8")
        reporter.log(f"[SAVE] JSON: {json_path}")
        reporter.log(f"[SAVE] TXT: {txt_path}")
        return str(json_path), str(txt_path)
    except OSError as exc:
        reporter.log(f"[SAVE] ERROR | {safe_error(exc)['category']} | stdout сохранён")
        return None, None


def main() -> int:
    reporter = Reporter()
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_SECONDS
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report: dict[str, Any] = {
        "diagnostic": "NSPD Standalone Diagnostic v1",
        "started_utc": now_utc(),
        "limits": {
            "tcp_attempt_seconds": TCP_TIMEOUT_SECONDS,
            "http_request_seconds": HTTP_TIMEOUT_SECONDS,
            "total_budget_seconds": TOTAL_BUDGET_SECONDS,
            "maximum_retries_per_api_request": 1,
            "delay_between_addresses_seconds": 1,
        },
        "endpoint": {"method": "GET", "url": API_URL, "thematicSearchId": 1},
        "searches": [],
    }
    reporter.log("NSPD Standalone Diagnostic v1")
    reporter.log(f"START UTC: {report['started_utc']}")
    reporter.log(f"[API] GET {API_URL} | query=<public test address> | thematicSearchId=1")
    reporter.log()

    report["environment"] = environment_diagnostic(reporter)
    ssl_context: ssl.SSLContext | None = None
    ca_info: dict[str, Any] = {}
    try:
        ssl_context, ca_info = build_ssl_context(reporter)
        report["tls_context"] = {"status": "OK", **ca_info}
    except Exception as exc:
        error = safe_error(exc)
        report["tls_context"] = {"status": "ERROR", "error": error}
        reporter.log(f"[TLS-CA] ERROR | {error_log_details(error)}")
    report["dns"], records = dns_diagnostic(reporter, deadline)
    if not records:
        report["tcp"] = skipped("TCP", "DNS unavailable")
        report["tls"] = skipped("TLS", "DNS unavailable")
        report["http"] = skipped("HTTP", "DNS unavailable")
        report["api"] = skipped("API", "DNS unavailable")
    else:
        report["tcp"] = tcp_diagnostic(reporter, deadline, records)
        if report["tcp"]["status"] != "OK":
            report["tls"] = skipped("TLS", "TCP unavailable")
            report["http"] = skipped("HTTP", "TCP unavailable")
            report["api"] = skipped("API", "TCP unavailable")
        else:
            if ssl_context is None:
                report["tls"] = {
                    "status": "ERROR",
                    "attempts": [],
                    "error": report["tls_context"]["error"],
                }
            else:
                report["tls"] = tls_diagnostic(
                    reporter,
                    deadline,
                    records,
                    report["tcp"],
                    ssl_context,
                    ca_info,
                )
            if report["tls"]["status"] != "OK":
                report["http"] = skipped("HTTP", "TLS unavailable")
                report["api"] = skipped("API", "TLS unavailable")
            else:
                report["http"] = root_http_diagnostic(
                    reporter, deadline, ssl_context, ca_info
                )
                reporter.log("[API] Проверка поискового endpoint на публичных адресах")
                consecutive_technical_errors = 0
                stop_reason = ""
                for index, source in enumerate(TEST_ADDRESSES, start=1):
                    if time.monotonic() >= deadline:
                        stop_reason = "GLOBAL_DEADLINE"
                        break
                    if index > 1:
                        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
                    result = search_address(
                        reporter,
                        deadline,
                        index,
                        source,
                        ssl_context,
                        ca_info,
                    )
                    report["searches"].append(result)
                    status = result.get("status")
                    if status == 429:
                        stop_reason = "HTTP_429"
                        break
                    if status == 403:
                        stop_reason = "HTTP_403"
                        break
                    if result.get("json_parsed") and not result.get("schema_valid"):
                        stop_reason = "API_SCHEMA_MISMATCH"
                        break
                    if result.get("technical_success"):
                        consecutive_technical_errors = 0
                    else:
                        consecutive_technical_errors += 1
                        if consecutive_technical_errors >= 2:
                            stop_reason = "REPEATED_TECHNICAL_ERRORS"
                            break
                report["api"] = {
                    "status": "OK"
                    if any(item.get("technical_success") for item in report["searches"])
                    else "ERROR",
                    "stop_reason": stop_reason or None,
                    "requests_completed": len(report["searches"]),
                }
                if stop_reason:
                    reporter.log(f"[API] Дальнейшие запросы остановлены: {stop_reason}")

    report["http_403_comparison"] = compare_http_403_responses(report)
    log_http_403_summary(reporter, report["http_403_comparison"])
    report["finished_utc"] = now_utc()
    report["summary"] = make_summary(report, time.monotonic() - started)
    print_summary(reporter, report["summary"])
    json_path, txt_path = save_reports(reporter, report, timestamp)
    report["output_files"] = {"json": json_path, "txt": txt_path}
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
