import pytest

from news_super_analyser.giga_text import (
    GIGA_TEXT_PROMPT,
    GigaTextProcessor,
    build_giga_text_user_prompt,
    validate_plain_text_response,
)
from news_super_analyser.gigachat_client import GigaChatError, GigaChatModelRefusal


RESULT = """Тип инцидента: Пожар

Краткая сводка: Повреждён жилой дом.

Объект 1:
- Название: Жилой дом
- Тип объекта: Дом
- Организация: Не указано
- Регион: Белгородская область
- Населённый пункт: Дубовое
- Адрес: Не указано
- Кадастровый номер: Не указано
- Количество: 1
- Повреждения: Повреждена кровля
- Подтверждающая цитата: «повреждена кровля дома»"""


class Gateway:
    def __init__(self, candidates, statuses=None):
        self.candidates = candidates
        self.statuses = list(statuses or [])
        self.read_calls = []
        self.writes = []

    def read_giga_text_candidates(self, limit, *, record_key=None):
        self.read_calls.append((limit, record_key))
        return self.candidates[:limit]

    def write_giga_text_result(self, *, record_key, result):
        self.writes.append((record_key, result))
        return self.statuses.pop(0)


class Client:
    def __init__(self, response=RESULT, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def candidate(text="Полный текст новости", key="-1:42"):
    return {
        "record_key": key,
        "full_text": text,
        "source_cell": "M2",
        "target_cell": "Q2",
    }


def test_prompt_is_exact_plain_text_contract_and_includes_article():
    prompt = build_giga_text_user_prompt("Текст статьи")

    assert prompt.startswith(GIGA_TEXT_PROMPT)
    assert prompt.endswith("Article:\nТекст статьи")
    assert 'Use "Не указано" for unknown values.' in prompt
    assert "not JSON or Markdown code blocks" in prompt


def test_success_uses_m_and_writes_full_response_once():
    gateway = Gateway([candidate()], ["updated"])
    client = Client()

    stats = GigaTextProcessor(gateway, client, sleep=lambda _: None).run(
        1, record_key="-1:42"
    )

    assert gateway.read_calls == [(1, "-1:42")]
    assert gateway.writes == [("-1:42", RESULT)]
    assert client.calls[0]["user_prompt"].endswith("Article:\nПолный текст новости")
    assert stats.updated == 1


def test_empty_m_does_not_call_model_or_write():
    gateway = Gateway([candidate("   ")])
    client = Client()

    stats = GigaTextProcessor(gateway, client).run(1)

    assert client.calls == []
    assert gateway.writes == []
    assert stats.no_full_text == 1


def test_existing_q_race_is_not_overwritten():
    gateway = Gateway([candidate()], ["already_processed"])
    stats = GigaTextProcessor(gateway, Client(), sleep=lambda _: None).run(1)

    assert stats.already_processed == 1
    assert stats.updated == 0


@pytest.mark.parametrize(
    "response",
    ["", "   ", '{"incident_type":"пожар"}', "```text\nОтвет\n```"],
)
def test_invalid_model_output_never_reaches_q(response):
    gateway = Gateway([candidate()])
    stats = GigaTextProcessor(
        gateway, Client(response=response), sleep=lambda _: None
    ).run(1)

    assert gateway.writes == []
    assert stats.model_errors == 1


@pytest.mark.parametrize(
    "error",
    [
        GigaChatError("GigaChat timeout", retryable=False),
        GigaChatModelRefusal(
            "GigaChat thematic restriction: finish_reason=blacklist"
        ),
    ],
)
def test_api_error_or_blacklist_leaves_q_empty(error):
    gateway = Gateway([candidate()])
    stats = GigaTextProcessor(
        gateway, Client(error=error), sleep=lambda _: None
    ).run(1)

    assert gateway.writes == []
    assert stats.model_errors == 1


def test_transient_error_retries_then_writes_once():
    class TransientClient:
        def __init__(self):
            self.calls = 0

        def complete(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise GigaChatError("temporary", retryable=True)
            return RESULT

    gateway = Gateway([candidate()], ["updated"])
    client = TransientClient()
    stats = GigaTextProcessor(gateway, client, sleep=lambda _: None).run(1)

    assert client.calls == 2
    assert len(gateway.writes) == 1
    assert stats.updated == 1


def test_write_error_does_not_create_second_result():
    class BrokenGateway(Gateway):
        def write_giga_text_result(self, *, record_key, result):
            self.writes.append((record_key, result))
            raise RuntimeError("write failed")

    gateway = BrokenGateway([candidate()])
    stats = GigaTextProcessor(gateway, Client(), sleep=lambda _: None).run(1)

    assert len(gateway.writes) == 1
    assert stats.write_errors == 1
