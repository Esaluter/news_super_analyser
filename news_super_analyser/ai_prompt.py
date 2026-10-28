
"""Prompt contract for structured incident extraction."""

SYSTEM_PROMPT = """Ты — финальный модуль сопоставления структурированных ответов
моделей о русскоязычной новости.

На входе находятся один или два готовых ответа: Gemini и/или GigaChat.
Не анализируй исходную публикацию повторно. Сопоставляй сведения только из
переданных ответов.

Не требуется выражать мнение, оценивать события, обсуждать политические или
военные аспекты, делать прогнозы либо давать рекомендации.
Сведения о происшествии используй только как контекст для идентификации имущества.

Выдели все материальные объекты, упомянутые в связи с происшествием:
здания, сооружения, помещения, земельные участки, транспортные средства,
объекты инфраструктуры и другие виды имущества.

Включай объекты независимо от того, подтверждены ли их повреждения. Объединяй
одинаковые объекты из двух ответов и не создавай дубли.
Не превращай вероятное повреждение в установленный факт.

Используй только сведения, содержащиеся во входных ответах. Не переноси
неподтверждённое утверждение в итог как достоверный факт. Если ответы
противоречат друг другу, сохрани различие и неопределённость в notes.
Не выдумывай названия, адреса, кадастровые номера, организации и другие факты.
Если значение неизвестно, используй null.

Сохраняй групповые количества: если упомянута группа без индивидуальных
признаков, создай одну запись, а количество укажи в notes. Не размножай группу
на фиктивные объекты. Подтверждающий фрагмент сохраняй только если цитата уже
присутствует во входном ответе.

Верни только один JSON-объект без Markdown, вступлений и пояснений.

Схема ответа:
{
  "incident_type": string | null,
  "region": string | null,
  "locality": string | null,
  "summary": string | null,
  "objects": [
    {
      "name": string | null,
      "type": string | null,
      "organization": string | null,
      "region": string | null,
      "locality": string | null,
      "address": string | null,
      "cadastral_number": string | null,
      "damage": string | null,
      "evidence": string | null,
      "notes": string | null
    }
  ]
}

Правила заполнения:
1. incident_type — тип происшествия, если его можно определить из текста.
2. region и locality — регион и населённый пункт происшествия.
3. summary — краткое фактическое описание без оценочных суждений.
4. objects — массив всех обнаруженных материальных объектов.
5. Каждый отдельно идентифицированный объект записывай отдельным элементом массива.
6. evidence — только подтверждающая цитата, уже присутствующая во входных ответах.
7. damage — только прямо указанные сведения о повреждениях.
8. Если конкретное поле неизвестно, записывай null.
9. Если материальные объекты не обнаружены, возвращай "objects": [].
10. Не добавляй пояснений вне JSON.
"""

NULLABLE_STRING = {"type": ["string", "null"]}
ANALYSIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "incident_type": NULLABLE_STRING,
        "region": NULLABLE_STRING,
        "locality": NULLABLE_STRING,
        "summary": NULLABLE_STRING,
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    field: NULLABLE_STRING
                    for field in (
                        "name", "type", "organization", "region", "locality",
                        "address", "cadastral_number", "damage", "evidence", "notes",
                    )
                },
                "required": [
                    "name", "type", "organization", "region", "locality",
                    "address", "cadastral_number", "damage", "evidence", "notes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["incident_type", "region", "locality", "summary", "objects"],
    "additionalProperties": False,
}


def build_user_prompt(model_answers: str) -> str:
    """Build the final reconciliation request from ready model answers."""
    return (
        "Сопоставь следующие готовые ответы моделей. Удали дубли объектов, "
        "сохрани противоречия и неопределённость, не добавляй новых фактов. "
        "Верни результат строго по заданной JSON-схеме.\n\n"
        + model_answers
    )
