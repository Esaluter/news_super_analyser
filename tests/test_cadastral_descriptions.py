from __future__ import annotations

from news_super_analyser.cadastral_enrichment import CadastralLimits
from news_super_analyser.cadastral_queue import LocalCadastralQueueProcessor


OBJECT_ID = "123e4567-e89b-42d3-a456-426614174000|1"
NSPD_ADDRESS = "Белгородская область, Белгород, ул. Садовая, д. 5"


class Gateway:
    def __init__(self):
        self.updates = []

    def read_cadastral_tasks(self, _limit):
        return {"total_records": 1, "tasks": [task()]}

    def update_cadastral_task(self, **values):
        self.updates.append(values)
        return "updated"


class Searcher:
    timeout = 10.0

    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, _query, **_kwargs):
        return self.candidates


def task():
    return {
        "object_id": OBJECT_ID,
        "region": "Белгородская область",
        "locality": "Белгород",
        "address": "ул. Садовая, д. 5",
        "cadastral_number": "",
        "cadastral_quarter": "",
        "all_cadastral": "",
        "status": "ОЖИДАЕТ",
        "error": "",
        "checked_at": "",
    }


def candidate(
    number,
    *,
    quarter="",
    category="",
    building_name="",
    purpose="",
    permitted_use="",
):
    return {
        "cadastral_number": number,
        "cadastral_quarter": quarter,
        "address": NSPD_ADDRESS,
        "category_name": category,
        "building_name": building_name,
        "purpose": purpose,
        "permitted_use": permitted_use,
    }


def run(candidates):
    gateway = Gateway()
    processor = LocalCadastralQueueProcessor(
        gateway,
        Searcher(candidates),
        limits=CadastralLimits(
            http_timeout_seconds=10,
            row_timeout_seconds=30,
            stage_timeout_seconds=120,
            attempts=1,
            request_delay_seconds=0,
        ),
    )
    processor.run(1)
    return gateway.updates[0]


def test_building_name_and_explicit_quarter_have_priority():
    update = run(
        [
            candidate(
                "31:18:0803014:128",
                quarter="31:18:9999999",
                category="Здание",
                building_name="Летняя кухня",
            )
        ]
    )

    assert update["all_cadastral"] == (
        "31:18:0803014:128 — Здание (Летняя кухня)"
    )
    assert update["cadastral_quarter"] == "31:18:9999999"
    assert update["cadastral_number"] == "31:18:0803014:128"


def test_land_plot_uses_documented_permitted_use():
    update = run(
        [
            candidate(
                "31:18:0803035:25",
                category="Земельный участок",
                permitted_use="Для индивидуального жилищного строительства",
            )
        ]
    )

    assert update["all_cadastral"] == (
        "31:18:0803035:25 — Земельный участок "
        "(Для индивидуального жилищного строительства)"
    )


def test_missing_description_keeps_only_real_available_fields():
    update = run([candidate("31:18:0803035:24")])

    assert update["all_cadastral"] == "31:18:0803035:24"


def test_multiple_types_are_described_deduplicated_and_ambiguous():
    update = run(
        [
            candidate(
                "31:18:0803035:24",
                quarter="31:18:0803035",
                category="Земельный участок",
                purpose="Для строительства объекта здравоохранения",
            ),
            candidate(
                "31:18:0803035:42",
                quarter="31:18:0803035",
                category="Здание",
                building_name="Жилой дом",
            ),
            candidate(
                "31:18:0803035:42",
                quarter="31:18:0803035",
                category="Здание",
                building_name="Дубликат",
            ),
        ]
    )

    assert update["status"] == "НЕОДНОЗНАЧНО"
    assert update["cadastral_number"] == ""
    assert update["cadastral_quarter"] == "31:18:0803035"
    assert update["all_cadastral"].splitlines() == [
        "31:18:0803035:24 — Земельный участок "
        "(Для строительства объекта здравоохранения)",
        "31:18:0803035:42 — Здание (Жилой дом)",
    ]


def test_multiple_distinct_quarters_leave_primary_quarter_empty():
    update = run(
        [
            candidate(
                "31:18:0803035:24", quarter="31:18:0803035", category="Здание"
            ),
            candidate(
                "31:18:0803014:128", quarter="31:18:0803014", category="Здание"
            ),
        ]
    )

    assert update["status"] == "НЕОДНОЗНАЧНО"
    assert update["cadastral_quarter"] == ""
