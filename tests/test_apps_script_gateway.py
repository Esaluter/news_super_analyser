from __future__ import annotations

import requests
import pytest

from news_super_analyser.sheets import AppsScriptError, AppsScriptGateway
from news_super_analyser.sync import SOURCE_HEADERS, TARGET_HEADERS


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_http_gateway_reads_source_and_target_keys():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        action = kwargs["json"]["action"]
        if action == "read_source":
            return FakeResponse({"ok": True, "values": [list(SOURCE_HEADERS), ["1:1"]]})
        return FakeResponse(
            {"ok": True, "headers": list(TARGET_HEADERS), "keys": ["1:0"]}
        )

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)

    assert gateway.read_source_rows()[1][0] == "1:1"
    assert gateway.read_target_rows()[1] == ["1:0"]
    assert all(call[1]["json"]["secret"] == "secret" for call in calls)
    assert all(call[1]["allow_redirects"] is True for call in calls)


def test_http_gateway_accepts_inserted_and_exists_statuses():
    statuses = iter(("inserted", "exists"))

    def post(_url, **_kwargs):
        return FakeResponse({"ok": True, "status": next(statuses)})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    row = [""] * len(TARGET_HEADERS)
    gateway.append_target_row(row)
    gateway.append_target_row(row)


def test_wrong_secret_is_non_retryable_and_secret_is_not_in_error():
    calls = 0

    def post(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse({"ok": False, "error": "unauthorized"})

    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "top-secret", post=post, attempts=3
    )

    with pytest.raises(AppsScriptError, match="unauthorized") as caught:
        gateway.read_source_rows()

    assert calls == 1
    assert "top-secret" not in str(caught.value)


def test_transient_apps_script_error_is_retried():
    responses = iter(
        (
            FakeResponse({"ok": False, "error": "busy"}),
            FakeResponse({"ok": True, "headers": list(TARGET_HEADERS), "keys": []}),
        )
    )
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec",
        "secret",
        post=lambda *_args, **_kwargs: next(responses),
        attempts=2,
        sleep=lambda _seconds: None,
    )
    assert gateway.read_target_rows() == [list(TARGET_HEADERS)]


def test_http_429_exposes_retry_after_to_bounded_retry():
    responses = iter(
        (
            FakeResponse({}, status_code=429, headers={"Retry-After": "0"}),
            FakeResponse({"ok": True, "values": [list(SOURCE_HEADERS)]}),
        )
    )
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec",
        "secret",
        post=lambda *_args, **_kwargs: next(responses),
        attempts=2,
        sleep=lambda _seconds: None,
    )
    assert gateway.read_source_rows() == [list(SOURCE_HEADERS)]


def test_network_exception_is_sanitized():
    def post(_url, **_kwargs):
        raise requests.ConnectionError("message that could contain request details")

    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=post, attempts=1
    )

    with pytest.raises(AppsScriptError) as caught:
        gateway.read_source_rows()

    assert str(caught.value) == "Apps Script transport failure: ConnectionError"


def test_full_text_candidate_contract_and_limit():
    def post(_url, **kwargs):
        assert kwargs["json"]["action"] == "read_full_text_candidates"
        assert kwargs["json"]["limit"] == 1
        return FakeResponse(
            {
                "ok": True,
                "candidates": [
                    {
                        "record_key": "-1:42",
                        "links": "https://t.me/channel/42",
                        "source_text": "Исходный текст",
                    }
                ],
            }
        )

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)

    assert gateway.read_full_text_candidates(1) == [
        {
            "record_key": "-1:42",
            "links": "https://t.me/channel/42",
            "source_text": "Исходный текст",
        }
    ]
    with pytest.raises(ValueError, match="limit"):
        gateway.read_full_text_candidates(21)


def test_full_text_update_returns_conditional_status():
    calls = []

    def post(_url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse({"ok": True, "status": "already_processed"})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)

    status = gateway.update_full_text(
        record_key="-1:42",
        full_text="Текст",
        load_status="Загружено",
        loaded_at="27.10.2026 12:00:00",
    )

    assert status == "already_processed"
    assert calls[0]["record_key"] == "-1:42"
    assert calls[0]["full_text"] == "Текст"


def test_full_text_backfill_gateway_contract():
    calls = []

    def post(_url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        if body["action"] == "read_full_text_backfill_candidates":
            return FakeResponse({
                "ok": True,
                "candidates": [{"record_key": "-1:42"}],
            })
        return FakeResponse({"ok": True, "status": "updated"})

    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=post
    )

    assert gateway.read_full_text_backfill_candidates(1) == [
        {"record_key": "-1:42"}
    ]
    assert gateway.backfill_full_text(record_key="-1:42") == "updated"
    assert calls[0]["limit"] == 1
    assert calls[1]["record_key"] == "-1:42"


def test_gemini_formula_gateway_contract_and_conditional_status():
    calls = []

    def post(_url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        if body["action"] == "read_gemini_formula_candidates":
            return FakeResponse({
                "ok": True,
                "candidates": [{
                    "record_key": "-1:42",
                    "source_cell": "M7",
                    "target_cell": "P7",
                }],
            })
        return FakeResponse({"ok": True, "status": "already_processed"})

    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=post
    )

    assert gateway.read_gemini_formula_candidates(
        1, record_key="-1:42"
    ) == [{
        "record_key": "-1:42",
        "source_cell": "M7",
        "target_cell": "P7",
    }]
    assert gateway.prepare_gemini_formula(record_key="-1:42") == "already_processed"
    assert calls[0]["limit"] == 1
    assert calls[0]["record_key"] == "-1:42"
    assert calls[1]["record_key"] == "-1:42"


@pytest.mark.parametrize("limit", [0, 21])
def test_gemini_formula_gateway_rejects_invalid_limit(limit):
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=lambda *_a, **_k: None
    )
    with pytest.raises(ValueError, match="limit"):
        gateway.read_gemini_formula_candidates(limit)


def test_giga_text_gateway_contract_and_conditional_write():
    calls = []

    def post(_url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        if body["action"] == "read_giga_text_candidates":
            return FakeResponse({
                "ok": True,
                "candidates": [{
                    "record_key": "-1:42",
                    "full_text": "Полный текст",
                    "source_cell": "M7",
                    "target_cell": "Q7",
                }],
            })
        return FakeResponse({"ok": True, "status": "already_processed"})

    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=post
    )

    assert gateway.read_giga_text_candidates(1, record_key="-1:42") == [{
        "record_key": "-1:42",
        "full_text": "Полный текст",
        "source_cell": "M7",
        "target_cell": "Q7",
    }]
    assert gateway.write_giga_text_result(
        record_key="-1:42", result="Структурированный результат"
    ) == "already_processed"
    assert calls[0]["record_key"] == "-1:42"
    assert calls[1]["result"] == "Структурированный результат"


@pytest.mark.parametrize("limit", [0, 21])
def test_giga_text_gateway_rejects_invalid_limit(limit):
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec", "secret", post=lambda *_a, **_k: None
    )
    with pytest.raises(ValueError, match="limit"):
        gateway.read_giga_text_candidates(limit)


def test_ai_candidate_contract_specific_key():
    def post(_url, **kwargs):
        body = kwargs["json"]
        assert body["action"] == "read_ai_candidates"
        assert body["record_key"] == "-1:42"
        assert "force" not in body
        assert body["claim"] is False
        return FakeResponse({"ok": True, "candidates": [{
            "record_key": "-1:42",
            "news_datetime": "27.10.2026 12:00:00",
            "links": "https://t.me/channel/42",
            "gemini_text": "Ответ Gemini",
            "giga_text": "Ответ GigaChat",
            "source_models": "Gemini + GigaChat",
            "news_status": "Ожидает анализа",
            "claim_token": "",
        }]})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    result = gateway.read_ai_candidates(1, record_key="-1:42")
    assert result[0]["source_models"] == "Gemini + GigaChat"


@pytest.mark.parametrize("limit", [1, 5, 20])
def test_ai_candidate_limits_1_5_20(limit):
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec",
        "secret",
        post=lambda *_args, **_kwargs: FakeResponse({"ok": True, "candidates": []}),
    )
    assert gateway.read_ai_candidates(limit) == []


@pytest.mark.parametrize("limit", [0, 21])
def test_ai_candidate_invalid_limits_are_rejected(limit):
    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret")
    with pytest.raises(ValueError, match="between 1 and 20"):
        gateway.read_ai_candidates(limit)


def test_finalize_ai_candidate_contract():
    def post(_url, **kwargs):
        body = kwargs["json"]
        assert body["action"] == "finalize_ai_candidate"
        assert body["record_key"] == "-1:42"
        assert body["claim_token"] == "claim-id"
        assert body["final_status"] == "Обработано"
        return FakeResponse({"ok": True, "status": "updated"})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    assert gateway.finalize_ai_candidate(
        record_key="-1:42", claim_token="claim-id", final_status="Обработано"
    ) == "updated"


def test_ai_writes_accept_idempotent_statuses_and_counts():
    calls = []

    def post(_url, **kwargs):
        calls.append(kwargs["json"])
        if kwargs["json"]["action"] == "save_ai_response":
            return FakeResponse({"ok": True, "status": "exists"})
        return FakeResponse({
            "ok": True,
            "status": "completed",
            "analysis_inserted": 0,
            "objects_inserted": 1,
        })

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    assert gateway.save_ai_response(["x"] * 6) == "exists"
    result = gateway.save_ai_analysis(["x"] * 13, [["x"] * 14])
    assert result == {"status": "completed", "analysis_inserted": 0, "objects_inserted": 1}
    assert "response_row" in calls[0]
    assert "analysis_row" in calls[1]


def test_ai_gateway_requires_news_datetime_column_in_object_rows():
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec",
        "secret",
        post=lambda *_args, **_kwargs: FakeResponse({"ok": True, "status": "exists"}),
    )
    with pytest.raises(ValueError, match="14 strings"):
        gateway.save_ai_analysis(["x"] * 13, [["x"] * 13])


def test_ai_response_error_update_contract():
    def post(_url, **kwargs):
        body = kwargs["json"]
        assert body["action"] == "update_ai_response_error"
        assert body["expected_error"] == ""
        assert body["error"] == "invalid JSON"
        return FakeResponse({"ok": True, "status": "updated"})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    assert gateway.update_ai_response_error(
        analysis_id="id", expected_error="", error="invalid JSON"
    ) == "updated"


def test_ai_gateway_rejects_malformed_server_data():
    gateway = AppsScriptGateway(
        "https://script.google.com/test/exec",
        "secret",
        post=lambda *_args, **_kwargs: FakeResponse({"ok": True, "candidates": [{}]}),
    )
    with pytest.raises(AppsScriptError, match="invalid AI candidate"):
        gateway.read_ai_candidates(1)


def test_object_enrichment_gateway_contract_and_conditional_update():
    calls = []
    candidate = {
        "row_number": 7,
        "analysis_id": "123e4567-e89b-42d3-a456-426614174000",
        "object_number": "1",
        "name": "Склад",
        "organization": "ООО Ромашка",
        "region": "Белгородская область",
        "locality": "Белгород",
        "address": "ул. Садовая, д. 5",
        "cadastral_number": "",
        "cadastral_quarter": "",
        "all_cadastral": "",
        "gemini": "",
    }

    def post(_url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        if body["action"] == "read_object_enrichment_candidates":
            return FakeResponse({"ok": True, "candidates": [candidate]})
        return FakeResponse(
            {"ok": True, "status": "updated", "updated_fields": ["cadastral_number"]}
        )

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)
    assert gateway.read_object_enrichment_candidates(1, row_number=7) == [candidate]
    result = gateway.update_object_enrichment(
        analysis_id=candidate["analysis_id"],
        object_number="1",
        values={"cadastral_number": "31:16:0101001:25"},
        prepare_gemini=False,
    )

    assert result["updated_fields"] == ["cadastral_number"]
    assert calls[0]["row_number"] == 7
    assert calls[1]["values"]["cadastral_number"] == "31:16:0101001:25"


@pytest.mark.parametrize("limit", [0, 501])
def test_object_enrichment_gateway_rejects_invalid_limit(limit):
    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret")
    with pytest.raises(ValueError, match="limit"):
        gateway.read_object_enrichment_candidates(limit)


def test_cadastral_queue_gateway_contract():
    calls = []
    task = {
        "object_id": "123e4567-e89b-42d3-a456-426614174000|1",
        "region": "Регион",
        "locality": "Город",
        "address": "ул. Новая, д. 1",
        "cadastral_number": "",
        "cadastral_quarter": "",
        "all_cadastral": "",
        "status": "ОЖИДАЕТ",
        "error": "",
        "checked_at": "",
    }

    def post(_url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        if body["action"] == "sync_cadastral_queue":
            return FakeResponse(
                {
                    "ok": True,
                    "records_added": 1,
                    "results_received": 0,
                    "address_resets": 0,
                    "formulas_prepared": 1,
                    "objects_checked": 1,
                }
            )
        if body["action"] == "read_cadastral_tasks":
            return FakeResponse({"ok": True, "total_records": 1, "tasks": [task]})
        return FakeResponse({"ok": True, "status": "updated"})

    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret", post=post)

    assert gateway.sync_cadastral_queue()["records_added"] == 1
    assert gateway.read_cadastral_tasks(500)["tasks"] == [task]
    assert gateway.update_cadastral_task(
        object_id=task["object_id"],
        region=task["region"],
        locality=task["locality"],
        address=task["address"],
        expected_status="ОЖИДАЕТ",
        cadastral_number="31:16:1:1",
        cadastral_quarter="31:16:1",
        all_cadastral="31:16:1:1",
        status="НАЙДЕНО",
        error="",
        checked_at="30.10.2026 10:00:00",
    ) == "updated"
    assert [call["action"] for call in calls] == [
        "sync_cadastral_queue",
        "read_cadastral_tasks",
        "update_cadastral_result",
    ]


@pytest.mark.parametrize("limit", [0, 501])
def test_cadastral_queue_gateway_rejects_invalid_limit(limit):
    gateway = AppsScriptGateway("https://script.google.com/test/exec", "secret")
    with pytest.raises(ValueError, match="limit"):
        gateway.read_cadastral_tasks(limit)

