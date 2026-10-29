from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from news_super_analyser.ai_analysis import (
    AiAnalysisProcessor,
    AnalysisParseError,
    parse_analysis_json,
)
from news_super_analyser.gigachat_client import GigaChatError, GigaChatModelRefusal


VALID = json.dumps(
    {
        "incident_type": "обстрел",
        "region": "Белгородская область",
        "locality": "Дубовое",
        "summary": "Повреждены дом и автомобиль",
        "objects": [
            {
                "name": "Жилой дом",
                "type": "дом",
                "organization": None,
                "region": "Белгородская область",
                "locality": "Дубовое",
                "address": None,
                "cadastral_number": None,
                "damage": "повреждена кровля",
                "evidence": "повреждена кровля дома",
                "notes": None,
            },
            {
                "name": "Автомобиль",
                "type": "транспорт",
                "damage": "повреждён",
                "evidence": "повреждён автомобиль",
            },
        ],
    },
    ensure_ascii=False,
)


class Gateway:
    def __init__(self, candidates=None):
        self.candidates = candidates or []
        self.response_rows = []
        self.analysis_rows = []
        self.object_rows = []
        self.selection = None
        self.fail_response = False
        self.finalized = []

    def read_ai_candidates(self, limit, *, record_key=None, claim=False):
        self.selection = (limit, record_key, claim)
        return self.candidates[:limit]

    def finalize_ai_candidate(self, *, record_key, claim_token, final_status):
        self.finalized.append((record_key, claim_token, final_status))
        return "updated"

    def save_ai_response(self, row):
        if self.fail_response:
            raise RuntimeError("write failed")
        self.response_rows.append(row)
        return "inserted"

    def update_ai_response_error(self, *, analysis_id, expected_error, error):
        for row in self.response_rows:
            if row[0] == analysis_id:
                assert row[5] == expected_error
                row[5] = error
                return "updated"
        return "not_found"

    def save_ai_analysis(self, row, object_rows):
        self.analysis_rows.append(row)
        self.object_rows.extend(object_rows)
        return {"status": "completed", "analysis_inserted": 1, "objects_inserted": len(object_rows)}


class Client:
    def __init__(self, response=VALID, error=None):
        self.response = response
        self.error = error
        self.calls = 0
        self.call_args = []

    def complete(self, **kwargs):
        self.calls += 1
        self.call_args.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def candidate(gemini_text="Ответ Gemini", giga_text="", **overrides):
    source_models = (
        "Gemini + GigaChat"
        if gemini_text and giga_text
        else ("Gemini" if gemini_text else ("GigaChat" if giga_text else ""))
    )
    value = {
        "record_key": "-1:42",
        "news_datetime": "27.10.2026 12:00:00",
        "links": "https://t.me/channel/42",
        "gemini_text": gemini_text,
        "giga_text": giga_text,
        "source_models": source_models,
        "news_status": "Ожидает анализа",
        "claim_token": "",
    }
    value.update(overrides)
    return value


def processor(tmp_path, gateway, client, **kwargs):
    return AiAnalysisProcessor(
        gateway,
        client,
        output_dir=tmp_path / "responses",
        now=lambda: datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc),
        sleep=lambda _: None,
        **kwargs,
    )


def test_parser_accepts_fence_and_preserves_missing_fields_as_null():
    parsed = parse_analysis_json("```json\n" + VALID + "\n```")
    assert len(parsed["objects"]) == 2
    assert parsed["objects"][1]["address"] is None
    assert parsed["objects"][1]["cadastral_number"] is None
    assert parse_analysis_json("Ответ:\n" + VALID + "\nКонец")["objects"][0]["name"] == "Жилой дом"


def test_parser_rejects_invalid_json_and_invalid_objects():
    with pytest.raises(AnalysisParseError, match="valid JSON"):
        parse_analysis_json("not json")
    with pytest.raises(AnalysisParseError, match="array"):
        parse_analysis_json('{"objects": null}')


def test_success_saves_raw_first_analysis_and_multiple_objects(tmp_path):
    gateway = Gateway([candidate()])
    result = processor(tmp_path, gateway, Client()).run()[0]

    assert result.status == "success"
    assert result.objects == 2
    assert gateway.response_rows[0][4] == VALID
    assert gateway.analysis_rows[0][1] == "-1:42"
    assert gateway.analysis_rows[0][5] == "Успешно"
    assert gateway.analysis_rows[0][10] == "2"
    assert gateway.analysis_rows[0][11] == ""
    assert [row[1] for row in gateway.object_rows] == ["1", "2"]
    assert {row[2] for row in gateway.object_rows} == {"Gemini"}
    assert gateway.object_rows[1][8] == ""
    assert gateway.object_rows[1][9] == ""
    assert {row[13] for row in gateway.object_rows} == {"27.10.2026 12:00:00"}
    assert all(len(row) == 14 for row in gateway.object_rows)


def test_missing_news_datetime_stays_blank_for_every_object(tmp_path):
    gateway = Gateway([candidate(news_datetime="")])
    result = processor(tmp_path, gateway, Client()).run()[0]

    assert result.status == "success"
    assert gateway.analysis_rows[0][2] == ""
    assert {row[13] for row in gateway.object_rows} == {""}


def test_success_with_no_objects(tmp_path):
    gateway = Gateway([candidate()])
    client = Client('{"incident_type":null,"region":null,"locality":null,"summary":null,"objects":[]}')
    result = processor(tmp_path, gateway, client).run()[0]
    assert result.status == "success"
    assert gateway.analysis_rows[0][10] == "0"
    assert gateway.object_rows == []


def test_invalid_json_preserves_raw_and_writes_error_analysis(tmp_path):
    gateway = Gateway([candidate()])
    result = processor(tmp_path, gateway, Client("invalid response")).run()[0]
    assert result.status == "error"
    assert gateway.response_rows[0][4] == "invalid response"
    assert "valid JSON" in gateway.response_rows[0][5]
    assert gateway.analysis_rows[0][5] == "Ошибка"
    assert gateway.analysis_rows[0][10] == "0"


def test_gigachat_error_is_recorded_without_objects(tmp_path):
    gateway = Gateway([candidate()])
    result = processor(
        tmp_path, gateway, Client(error=GigaChatError("GigaChat timeout"))
    ).run()[0]
    assert result.status == "error"
    assert gateway.response_rows[0][4] == ""
    assert gateway.response_rows[0][5] == "GigaChat timeout"
    assert gateway.object_rows == []


def test_final_analysis_logs_sanitized_item_error(tmp_path, caplog):
    gateway = Gateway([candidate()])
    subject = processor(
        tmp_path,
        gateway,
        Client(error=GigaChatError("timeout token=hidden https://internal/path")),
    )

    with caplog.at_level("WARNING"):
        result = subject.run()[0]

    assert result.status == "error"
    assert result.record_key in caplog.text
    assert "причина=timeout" in caplog.text
    assert "hidden" not in caplog.text
    assert "https://internal" not in caplog.text


def test_no_ready_model_answers_does_not_call_model(tmp_path):
    gateway = Gateway([candidate("")])
    client = Client()
    result = processor(tmp_path, gateway, client).run()[0]
    assert result.status == "error"
    assert client.calls == 0
    assert gateway.analysis_rows[0][5] == "Ошибка"


def test_selection_passes_specific_key(tmp_path):
    gateway = Gateway([])
    proc = processor(tmp_path, gateway, Client())
    assert proc.select(limit=1, record_key="-1:42") == []
    assert gateway.selection == (1, "-1:42", False)


def test_no_candidate_means_no_model_call_or_write(tmp_path):
    gateway = Gateway([])
    client = Client()
    assert processor(tmp_path, gateway, client).run() == []
    assert client.calls == 0
    assert gateway.response_rows == []


def test_response_write_failure_creates_local_recovery_file(tmp_path):
    gateway = Gateway([candidate()])
    gateway.fail_response = True
    result = processor(tmp_path, gateway, Client()).run()[0]
    assert result.status == "error"
    files = list((tmp_path / "responses").glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["raw_response"] == VALID
    assert gateway.analysis_rows == []


def test_input_over_sheet_limit_is_not_sent_to_model_and_is_saved_locally(tmp_path):
    gateway = Gateway([candidate("x" * 50_001)])
    client = Client()
    result = processor(tmp_path, gateway, client).run()[0]
    assert result.status == "error"
    assert client.calls == 0
    assert gateway.response_rows[0][3] == ""
    assert "сохранены локально" in gateway.response_rows[0][5]


def test_formula_only_gemini_and_empty_q_are_ignored(tmp_path):
    gateway = Gateway([candidate('=AI("prompt";M2)')])
    client = Client()
    result = processor(tmp_path, gateway, client).run()[0]
    assert result.status == "error"
    assert result.text_source == ""
    assert client.calls == 0
    assert gateway.response_rows[0][3] == ""
    assert gateway.response_rows[0][5] == "Готовые ответы Gemini/GigaChat отсутствуют"
    assert gateway.analysis_rows[0][5] == "Ошибка"


def test_both_ready_answers_are_compared_and_marked_with_combined_provenance(tmp_path):
    gateway = Gateway([candidate("Ответ Gemini", "Ответ GigaChat")])
    result = processor(tmp_path, gateway, Client()).run()[0]
    assert result.text_source == "Gemini + GigaChat"
    assert gateway.response_rows[0][3] == (
        "[Gemini]\nОтвет Gemini\n\n[GigaChat]\nОтвет GigaChat"
    )
    assert {row[2] for row in gateway.object_rows} == {"Gemini + GigaChat"}


def test_only_gigachat_answer_is_supported(tmp_path):
    gateway = Gateway([candidate("", "Ответ GigaChat")])
    result = processor(tmp_path, gateway, Client()).run()[0]
    assert result.status == "success"
    assert result.text_source == "GigaChat"
    assert {row[2] for row in gateway.object_rows} == {"GigaChat"}


def test_group_quantity_and_missing_address_are_preserved_in_one_object(tmp_path):
    grouped = json.dumps(
        {
            "incident_type": "обстрел",
            "region": "Белгородская область",
            "locality": None,
            "summary": "Повреждены восемь домов",
            "objects": [{
                "name": "Группа жилых домов",
                "type": "жилые дома",
                "organization": None,
                "region": "Белгородская область",
                "locality": None,
                "address": None,
                "cadastral_number": None,
                "damage": "повреждены",
                "evidence": "повреждены восемь домов",
                "notes": "Групповая запись, количество: 8",
            }],
        },
        ensure_ascii=False,
    )
    gateway = Gateway([candidate("Ответ Gemini", "Ответ GigaChat")])

    result = processor(tmp_path, gateway, Client(grouped)).run()[0]

    assert result.status == "success"
    assert len(gateway.object_rows) == 1
    assert gateway.object_rows[0][8] == ""
    assert gateway.object_rows[0][12] == "Групповая запись, количество: 8"


def test_conflict_between_models_is_preserved_as_uncertainty_without_duplicate(tmp_path):
    conflicted = json.dumps(
        {
            "incident_type": "пожар",
            "region": None,
            "locality": None,
            "summary": "Ответы расходятся в оценке повреждений",
            "objects": [{
                "name": "Склад",
                "type": "складское здание",
                "organization": None,
                "region": None,
                "locality": None,
                "address": None,
                "cadastral_number": None,
                "damage": "не подтверждено",
                "evidence": None,
                "notes": "Gemini сообщает повреждение, GigaChat его не подтверждает",
            }],
        },
        ensure_ascii=False,
    )
    gateway = Gateway([candidate("Ответ Gemini", "Ответ GigaChat")])

    result = processor(tmp_path, gateway, Client(conflicted)).run()[0]

    assert result.status == "success"
    assert len(gateway.object_rows) == 1
    assert "не подтверждает" in gateway.object_rows[0][12]


def test_claim_is_finalized_only_after_saved_result(tmp_path):
    item = candidate(claim_token="123e4567-e89b-42d3-a456-426614174999")
    gateway = Gateway([item])
    result = processor(tmp_path, gateway, Client()).run()[0]
    assert result.status == "success"
    assert gateway.finalized == [
        ("-1:42", item["claim_token"], "Обработано")
    ]


def test_failed_analysis_finalizes_error_and_next_candidate_continues(tmp_path):
    first = candidate(
        "Первая новость",
        record_key="-1:41",
        claim_token="123e4567-e89b-42d3-a456-426614174991",
    )
    second = candidate(
        "Вторая новость",
        record_key="-1:42",
        claim_token="123e4567-e89b-42d3-a456-426614174992",
    )
    gateway = Gateway([first, second])

    class SequentialClient:
        def __init__(self):
            self.calls = 0
        def complete(self, **_kwargs):
            self.calls += 1
            return "invalid" if self.calls == 1 else VALID

    results = processor(tmp_path, gateway, SequentialClient()).run(limit=2)
    assert [item.status for item in results] == ["error", "success"]
    assert [item[2] for item in gateway.finalized] == ["Ошибка анализа", "Обработано"]


def test_empty_pq_never_uses_m_link_or_source_text(tmp_path):
    gateway = Gateway([candidate("", "", full_text="Не использовать M")])
    client = Client()
    result = processor(tmp_path, gateway, client).run()[0]
    assert result.status == "error"
    assert client.calls == 0
    assert gateway.response_rows[0][3] == ""


def test_model_refusal_preserves_provider_text_and_is_an_error(tmp_path):
    refusal = GigaChatModelRefusal(
        "GigaChat thematic restriction: finish_reason=blacklist",
        raw_response="refusal payload",
    )
    gateway = Gateway([candidate()])
    client = Client(error=refusal)
    result = processor(tmp_path, gateway, client).run()[0]

    assert result.status == "error"
    assert gateway.response_rows[0][4] == "refusal payload"
    assert "finish_reason=blacklist" in gateway.response_rows[0][5]
    assert gateway.analysis_rows[0][5] == "Ошибка"
    assert gateway.response_rows[0][0] == result.analysis_id
    assert gateway.analysis_rows[0][0] == result.analysis_id
    assert gateway.object_rows == []
    assert gateway.finalized == []
    assert client.calls == 1


def test_ready_model_answer_is_sent_without_publication_normalization(tmp_path):
    original = (
        "Информация о происшествии\n\n"
        "Информация о происшествии\n"
        "VK | Одноклассники | MAX | Telegram\n"
        "В посёлке повреждена кровля дома по адресу ул. Центральная, 1"
    )
    item = candidate(original)
    gateway = Gateway([item])
    client = Client()

    result = processor(tmp_path, gateway, client).run()[0]

    assert result.status == "success"
    assert item["gemini_text"] == original
    assert original in client.call_args[0]["user_prompt"]
    assert "VK | Одноклассники" in client.call_args[0]["user_prompt"]
    assert gateway.response_rows[0][3] == "[Gemini]\n" + original


def test_transient_model_error_is_retried_before_single_history_write(tmp_path):
    class TransientClient:
        def __init__(self):
            self.calls = 0

        def complete(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise GigaChatError("temporary timeout", retryable=True)
            return VALID

    gateway = Gateway([candidate()])
    client = TransientClient()
    result = processor(tmp_path, gateway, client).run()[0]

    assert result.status == "success"
    assert client.calls == 2
    assert len(gateway.response_rows) == 1
    assert len(gateway.analysis_rows) == 1
