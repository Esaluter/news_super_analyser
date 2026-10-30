from __future__ import annotations

import io
import json
import ssl
import urllib.error
from datetime import datetime, timezone

import pytest

from news_super_analyser import nspd_http
from news_super_analyser.cadastral_enrichment import CadastralLimits
from news_super_analyser.cadastral_queue import LocalCadastralQueueProcessor
from news_super_analyser.nspd_debug import NspdDebugRecorder


class Response:
    status = 200

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def _payload(number: str = "31:16:0101001:25") -> bytes:
    return json.dumps(
        {
            "unknown_top_level": {"kept": True},
            "token": "must-not-leak",
            "data": {
                "features": [
                    {
                        "id": "feature-1",
                        "properties": {
                            "category_name": "Здание",
                            "options": {
                                "cad_number": number,
                                "readable_address": (
                                    "Белгородская область, Белгород, "
                                    "ул. Садовая, д. 5"
                                ),
                            },
                        },
                    }
                ]
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _recorder(tmp_path):
    return NspdDebugRecorder(
        tmp_path / "nspd_debug",
        mode="queue",
        now=lambda: datetime(2026, 10, 30, 14, 30, tzinfo=timezone.utc),
    )


def _search(searcher, query="Белгород, ул. Садовая, д. 5"):
    return searcher.search(
        query,
        region="Белгородская область",
        locality="Белгород",
        address="ул. Садовая, д. 5",
    )


def test_debug_saves_full_pre_parser_json_and_multiple_responses(monkeypatch, tmp_path):
    bodies = [_payload(), json.dumps({"data": {"features": []}}).encode()]

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 10
            return Response(bodies.pop(0))

    recorder = _recorder(tmp_path)
    recorder.begin_task({"object_id": "id|1"})
    monkeypatch.setattr(
        nspd_http.urllib.request, "build_opener", lambda *_args: Opener()
    )
    searcher = nspd_http.VerifiedNspdHttpSearcher(
        timeout=10,
        context=ssl.create_default_context(),
        debug_recorder=recorder,
    )

    assert _search(searcher)[0]["cadastral_number"] == "31:16:0101001:25"
    assert _search(searcher, "second") == []
    run_dir = recorder.finish()

    assert run_dir is not None
    files = sorted(run_dir.glob("request_*.json"))
    assert [path.name for path in files] == ["request_001.json", "request_002.json"]
    first = json.loads(files[0].read_text(encoding="utf-8"))
    assert first["response"]["body"]["unknown_top_level"] == {"kept": True}
    assert first["response"]["body"]["token"] == "[REDACTED]"
    assert first["parameters"]["thematicSearchId"] == "1"
    assert "must-not-leak" not in files[0].read_text(encoding="utf-8")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["http_request_count"] == 2


def test_debug_saves_http_error_and_non_json_body(monkeypatch, tmp_path):
    raw_body = b"<html>denied</html>"

    class Opener:
        def open(self, request, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {}, io.BytesIO(raw_body)
            )

    recorder = _recorder(tmp_path)
    monkeypatch.setattr(
        nspd_http.urllib.request, "build_opener", lambda *_args: Opener()
    )
    searcher = nspd_http.VerifiedNspdHttpSearcher(
        context=ssl.create_default_context(), debug_recorder=recorder
    )

    with pytest.raises(nspd_http.NspdHttpError):
        _search(searcher)
    run_dir = recorder.finish()

    request = json.loads(
        (run_dir / "request_001.json").read_text(encoding="utf-8")
    )
    assert request["http_status"] == 403
    assert request["error"] == "HTTP_403"
    assert request["response"]["is_json"] is False
    raw_file = run_dir / request["response"]["raw_body_file"]
    assert raw_file.read_bytes() == raw_body


def test_normal_search_does_not_create_debug_files(monkeypatch, tmp_path):
    class Opener:
        def open(self, _request, timeout):
            del timeout
            return Response(json.dumps({"data": {"features": []}}).encode())

    monkeypatch.setattr(
        nspd_http.urllib.request, "build_opener", lambda *_args: Opener()
    )
    searcher = nspd_http.VerifiedNspdHttpSearcher(
        context=ssl.create_default_context()
    )

    assert _search(searcher) == []
    assert list(tmp_path.iterdir()) == []


def test_summary_contains_existing_algorithm_diagnostics(monkeypatch, tmp_path):
    class Opener:
        def open(self, _request, timeout):
            del timeout
            return Response(_payload())

    class ReadOnlyGateway:
        def update_cadastral_task(self, **_values):
            raise AssertionError("diagnostic mode must not write")

    task = {
        "object_id": "123e4567-e89b-42d3-a456-426614174000|1",
        "region": "Белгородская область",
        "locality": "Белгород",
        "address": "ул. Садовая, д. 5",
        "cadastral_number": "",
        "cadastral_quarter": "",
        "all_cadastral": "",
        "status": "НАЙДЕНО",
        "error": "",
        "checked_at": "",
    }
    recorder = _recorder(tmp_path)
    monkeypatch.setattr(
        nspd_http.urllib.request, "build_opener", lambda *_args: Opener()
    )
    searcher = nspd_http.VerifiedNspdHttpSearcher(
        context=ssl.create_default_context(), debug_recorder=recorder
    )
    processor = LocalCadastralQueueProcessor(
        ReadOnlyGateway(),
        searcher,
        limits=CadastralLimits(
            http_timeout_seconds=10,
            row_timeout_seconds=30,
            stage_timeout_seconds=120,
            attempts=1,
            request_delay_seconds=0,
        ),
        diagnostics=recorder,
    )

    evaluation = processor.diagnose_task(task)
    run_dir = recorder.finish()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert evaluation.status == "НАЙДЕНО"
    assert summary["object_id"] == task["object_id"]
    assert summary["candidates_before_filter"] == 1
    assert summary["candidates_after_filter"] == 1
    assert summary["cadastral_numbers"] == ["31:16:0101001:25"]
    assert summary["cadastral_quarters"] == ["31:16:0101001"]
    assert {item["value"] for item in summary["available_object_types"]} >= {
        "Здание"
    }
    assert {item["value"] for item in summary["available_identifiers"]} >= {
        "feature-1",
        "31:16:0101001:25",
    }

