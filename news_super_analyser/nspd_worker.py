"""Isolated, retry-free and TLS-verified NSPD request worker."""

from __future__ import annotations

import json
import math
import ssl
import sys
from typing import Any

from .cadastral_enrichment import extract_candidates


def _verified_client(timeout_seconds: float) -> Any:
    import pynspd._sync.api as pynspd_sync

    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context is unavailable")
    # pynspd 1.1.15 imports its module-level SSL_CONTEXT into this module.
    # Replace it only inside this short-lived worker process.
    pynspd_sync.SSL_CONTEXT = context
    return pynspd_sync.Nspd(
        client_timeout=max(1, math.ceil(timeout_seconds)),
        client_retries=0,
        trust_env=False,
    )


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    try:
        timeout_seconds = float(payload.get("timeout_seconds", 0))
    except (TypeError, ValueError):
        timeout_seconds = 0
    if not query or timeout_seconds <= 0:
        return {"ok": False, "error_type": "invalid_worker_input"}
    client = None
    try:
        client = _verified_client(timeout_seconds)
        return {"ok": True, "candidates": extract_candidates(client.search(query))}
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__}
    finally:
        if client is not None:
            client.close()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (TypeError, json.JSONDecodeError):
        payload = {}
    result = execute(payload if isinstance(payload, dict) else {})
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
