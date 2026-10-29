"""Safe GigaChat DNS/TLS diagnostics; network use requires ``--network``."""

from __future__ import annotations

import argparse
import os
import platform
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

import truststore

from .config import load_project_environment
from .gigachat_client import AUTH_URL, CHAT_URL


ENDPOINTS = (AUTH_URL, CHAT_URL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Диагностика DNS и TLS GigaChat без OAuth и запросов к модели"
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="явно разрешить DNS и TLS handshake с двумя GigaChat endpoint",
    )
    return parser


def _context(ca_bundle: str | None) -> ssl.SSLContext:
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    return context


def _endpoint(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    return parsed.hostname or "", parsed.port or 443


def _network_check(host: str, port: int, context: ssl.SSLContext) -> str:
    addresses = sorted(
        {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )
    with socket.create_connection((host, port), timeout=15) as connection:
        with context.wrap_socket(connection, server_hostname=host) as tls:
            return (
                f"OK dns_addresses={len(addresses)} tls={tls.version()} "
                f"cipher={tls.cipher()[0]}"
            )


def _safe_failure(exc: BaseException) -> str:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return (
            f"{type(exc).__name__}: verify_code={exc.verify_code}; "
            f"verify_message={' '.join(exc.verify_message.split())[:240]}"
        )
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:240]}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_project_environment()
    configured = os.getenv("GIGACHAT_CA_BUNDLE", "").strip()
    ca_bundle = str(Path(configured).expanduser()) if configured else None
    bundle_exists = bool(ca_bundle and Path(ca_bundle).is_file())
    paths = ssl.get_default_verify_paths()
    print(f"Python: {platform.python_version()}; OpenSSL: {ssl.OPENSSL_VERSION}")
    print(
        "TLS verification: enabled; "
        f"custom_ca={'configured' if ca_bundle else 'not configured'}; "
        f"custom_ca_exists={bundle_exists}"
    )
    print(
        "Trust backend: truststore; default OpenSSL paths: "
        f"cafile_exists={bool(paths.cafile and Path(paths.cafile).is_file())}; "
        f"capath_exists={bool(paths.capath and Path(paths.capath).is_dir())}"
    )
    if ca_bundle and not bundle_exists:
        print("ERROR: configured GIGACHAT_CA_BUNDLE file does not exist", file=sys.stderr)
        return 2
    if not args.network:
        print("Network was not used. Add --network for DNS and verified TLS handshakes.")
        return 0

    failed = False
    for url in ENDPOINTS:
        host, port = _endpoint(url)
        try:
            result = _network_check(host, port, _context(ca_bundle))
        except Exception as exc:
            failed = True
            result = "FAILED " + _safe_failure(exc)
        print(f"{host}:{port} {result}")
    return 1 if failed else 0
