from __future__ import annotations

import io
import json
import ssl
import urllib.error

import pytest

from news_super_analyser import nspd_http


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


def test_local_http_search_uses_verified_context_and_parses_candidates(monkeypatch):
    captured = {}
    payload = json.dumps(
        {
            "data": {
                "features": [
                    {
                        "properties": {
                            "category": 5,
                            "category_name": "Здание",
                            "options": {
                                "cad_number": "31:16:0101001:25",
                                "readable_address": "Регион, Город, ул. Новая, д. 1",
                            },
                        }
                    }
                ]
            }
        },
        ensure_ascii=False,
    ).encode()

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response(payload)

    monkeypatch.setattr(nspd_http.urllib.request, "build_opener", lambda *_args: Opener())
    context = ssl.create_default_context()
    searcher = nspd_http.VerifiedNspdHttpSearcher(timeout=10, context=context)

    result = searcher.search(
        "Город, ул. Новая, д. 1",
        region="Регион",
        locality="Город",
        address="ул. Новая, д. 1",
        timeout_seconds=4,
    )

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert captured["timeout"] == 4
    assert "thematicSearchId=1" in captured["url"]
    assert result[0]["cadastral_number"] == "31:16:0101001:25"


def test_local_http_search_reports_403_without_disabling_tls(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {}, io.BytesIO(b"denied")
            )

    monkeypatch.setattr(nspd_http.urllib.request, "build_opener", lambda *_args: Opener())
    searcher = nspd_http.VerifiedNspdHttpSearcher(
        timeout=10, context=ssl.create_default_context()
    )

    with pytest.raises(nspd_http.NspdHttpError) as caught:
        searcher.search("query", region="", locality="", address="")

    assert caught.value.status == 403
