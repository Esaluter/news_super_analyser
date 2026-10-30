from __future__ import annotations

from news_super_analyser.cadastral_enrichment import CadastralLimits
from news_super_analyser.cadastral_queue import (
    CadastralQueueSynchronizer,
    LocalCadastralQueueProcessor,
)


OBJECT_ID = "123e4567-e89b-42d3-a456-426614174000|1"
ADDRESS = "ул. Садовая, д. 5"
NSPD_ADDRESS = "Белгородская область, Белгород, ул. Садовая, д. 5"


def task(**changes):
    value = {
        "object_id": OBJECT_ID,
        "region": "Белгородская область",
        "locality": "Белгород",
        "address": ADDRESS,
        "cadastral_number": "",
        "cadastral_quarter": "",
        "all_cadastral": "",
        "status": "ОЖИДАЕТ",
        "error": "",
        "checked_at": "",
    }
    value.update(changes)
    return value


class Gateway:
    def __init__(self, tasks, *, write_status="updated"):
        self.tasks = tasks
        self.write_status = write_status
        self.updates = []

    def read_cadastral_tasks(self, limit):
        return {"tasks": self.tasks[:limit], "total_records": len(self.tasks)}

    def update_cadastral_task(self, **values):
        self.updates.append(values)
        return self.write_status


class Searcher:
    timeout = 10.0

    def __init__(self, responses):
        self.responses = list(responses)

    def search(self, _query, **_kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def candidate(number):
    return {"cadastral_number": number, "address": NSPD_ADDRESS}


def limits():
    return CadastralLimits(
        http_timeout_seconds=10,
        row_timeout_seconds=30,
        stage_timeout_seconds=120,
        attempts=1,
        request_delay_seconds=0,
    )


def test_local_queue_saves_one_unambiguous_result():
    gateway = Gateway([task()])
    processor = LocalCadastralQueueProcessor(
        gateway, Searcher([[candidate("31:16:0101001:25")]]), limits=limits()
    )

    stats = processor.run(500)

    assert stats.found == 1
    assert stats.errors == 0
    assert gateway.updates[0]["status"] == "НАЙДЕНО"
    assert gateway.updates[0]["cadastral_number"] == "31:16:0101001:25"
    assert gateway.updates[0]["cadastral_quarter"] == "31:16:0101001"


def test_multiple_numbers_are_ambiguous_without_arbitrary_primary_number():
    gateway = Gateway([task()])
    processor = LocalCadastralQueueProcessor(
        gateway,
        Searcher(
            [[candidate("31:16:0101001:25"), candidate("31:16:0101001:26")]]
        ),
        limits=limits(),
    )

    stats = processor.run(500)
    update = gateway.updates[0]

    assert stats.ambiguous == 1
    assert update["status"] == "НЕОДНОЗНАЧНО"
    assert update["cadastral_number"] == ""
    assert update["cadastral_quarter"] == "31:16:0101001"
    assert update["all_cadastral"].splitlines() == [
        "31:16:0101001:25",
        "31:16:0101001:26",
    ]


def test_technical_error_preserves_old_values_and_next_task_continues():
    second_id = "223e4567-e89b-42d3-a456-426614174001|2"
    gateway = Gateway(
        [
            task(
                cadastral_number="31:16:0101001:1",
                cadastral_quarter="31:16:0101001",
                all_cadastral="31:16:0101001:1",
                status="ОШИБКА",
            ),
            task(object_id=second_id),
        ]
    )
    processor = LocalCadastralQueueProcessor(
        gateway,
        Searcher([ConnectionError("offline"), [candidate("31:16:0101001:25")]]),
        limits=limits(),
    )

    stats = processor.run(500)

    assert stats.checked == 2
    assert stats.errors == 1
    assert stats.found == 1
    assert gateway.updates[0]["status"] == "ОШИБКА"
    assert gateway.updates[0]["cadastral_number"] == "31:16:0101001:1"
    assert gateway.updates[1]["object_id"] == second_id


def test_stale_write_is_counted_as_skipped():
    gateway = Gateway([task()], write_status="stale")
    processor = LocalCadastralQueueProcessor(
        gateway, Searcher([[candidate("31:16:0101001:25")]]), limits=limits()
    )

    stats = processor.run(500)

    assert stats.skipped == 1
    assert stats.found == 0


def test_main_queue_sync_exposes_only_server_counts():
    class SyncGateway:
        def sync_cadastral_queue(self):
            return {
                "records_added": 2,
                "results_received": 1,
                "address_resets": 0,
                "formulas_prepared": 3,
                "objects_checked": 5,
            }

    result = CadastralQueueSynchronizer(SyncGateway()).run()

    assert result.records_added == 2
    assert result.results_received == 1
    assert result.errors == 0
