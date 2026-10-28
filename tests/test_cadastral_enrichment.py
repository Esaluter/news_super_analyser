from __future__ import annotations

from news_super_analyser.cadastral_enrichment import (
    OBJECT_GEMINI_PROMPT,
    CadastralEnrichmentProcessor,
    build_object_gemini_formula,
    build_search_variants,
    cadastral_quarter,
    candidate_rejection_reason,
    extract_candidates,
    filter_candidates,
    normalize_address,
    _house,
    _qualifier,
    _street,
)


ANALYSIS_ID = "123e4567-e89b-42d3-a456-426614174000"
BELGOROD_ADDRESS = "Белгородская область, г. Белгород, ул. Садовая, д. 5"


def row(**overrides):
    value = {
        "row_number": 2,
        "analysis_id": ANALYSIS_ID,
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
    value.update(overrides)
    return value


def candidate(number="31:16:0101001:25", address=BELGOROD_ADDRESS):
    return {"cadastral_number": number, "address": address}


class Gateway:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def read_object_enrichment_candidates(self, limit, *, row_number=None):
        rows = self.rows
        if row_number is not None:
            rows = [item for item in rows if item["row_number"] == row_number]
        return rows[:limit]

    def update_object_enrichment(self, **kwargs):
        self.updates.append(kwargs)
        fields = list(kwargs["values"])
        if kwargs["prepare_gemini"]:
            fields.append("gemini")
        return {"status": "updated", "updated_fields": fields}


class Searcher:
    def __init__(self, responses=None):
        self.responses = list(responses or [[]])
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def processor(gateway, searcher, attempts=1):
    return CadastralEnrichmentProcessor(
        gateway,
        searcher,
        attempts=attempts,
        request_delay=0,
        sleep=lambda _seconds: None,
    )


def test_english_gemini_prompt_and_current_row_range():
    formula = build_object_gemini_formula(27)

    assert OBJECT_GEMINI_PROMPT.startswith("Find one publicly available street address")
    assert "Найди" not in formula
    assert "'Not found'" in formula
    assert formula == f'=AI("{OBJECT_GEMINI_PROMPT}";D27:I27)'


def test_unambiguous_match_fills_j_o_p_and_empty_q():
    gateway = Gateway([row()])
    stats = processor(gateway, Searcher([[candidate()]])).run(1, apply=True)

    update = gateway.updates[0]
    assert update["values"] == {
        "all_cadastral": "31:16:0101001:25",
        "cadastral_number": "31:16:0101001:25",
        "cadastral_quarter": "31:16:0101001",
    }
    assert update["prepare_gemini"] is True
    assert stats.cells_updated == 4


def test_nonempty_q_and_other_manual_cells_are_never_overwritten():
    gateway = Gateway(
        [
            row(
                cadastral_number="-",
                cadastral_quarter="ручная правка",
                all_cadastral="31:16:0101001:99",
                gemini='=AI("old";D2:I2)',
            )
        ]
    )
    searcher = Searcher()

    stats = processor(gateway, searcher).run(1, apply=True)

    assert gateway.updates == []
    assert searcher.calls == []
    assert stats.cells_planned == 0


def test_moscow_duplicate_geo_and_address_markers_normalize_canonically():
    expected = "москва профсоюзная 123а строение 15"
    assert normalize_address(
        region="Москва",
        locality="Москва",
        address="Профсоюзная улица, дом 123А, строение 15",
    ) == expected
    assert normalize_address(
        region="г. Москва",
        locality="Москва",
        address="ул. Профсоюзная, д. 123А, стр. 15",
    ) == expected


def test_dots_commas_and_repeated_spaces_are_separators():
    assert normalize_address(
        region="Москва,",
        locality="Москва",
        address=" ул.   Профсоюзная, д. 123А, стр. 15 ",
    ) == "москва профсоюзная 123а строение 15"


def test_house_letter_is_preserved_and_not_equal_to_plain_number():
    assert normalize_address(
        region="Москва", locality="Москва", address="ул. Профсоюзная, д. 123А"
    ).endswith("123а")
    assert candidate_rejection_reason(
        {"cadastral_number": "77:06:0001:1", "address": "Москва, ул Профсоюзная, д 123"},
        region="Москва",
        locality="Москва",
        address="ул Профсоюзная, дом 123А",
    ) == "дом"


def test_different_corpus_and_building_are_rejected():
    assert candidate_rejection_reason(
        candidate(address="Белгородская область, Белгород, ул Садовая, д 5, корп 3"),
        region="Белгородская область",
        locality="Белгород",
        address="ул Садовая, дом 5, корпус 2",
    ) == "корпус"
    assert candidate_rejection_reason(
        candidate(address="Белгородская область, Белгород, ул Садовая, д 5, стр 1"),
        region="Белгородская область",
        locality="Белгород",
        address="ул Садовая, дом 5, строение 15",
    ) == "строение"


def test_empty_first_level_falls_back_to_normalized_level():
    gateway = Gateway([row()])
    searcher = Searcher([[], [candidate()]])

    stats = processor(gateway, searcher).run(1, apply=True)

    assert len(searcher.calls) == 2
    assert stats.search_diagnostics[0].attempts[0].status == "empty"
    assert stats.search_diagnostics[0].attempts[1].status == "matched"


def test_equivalent_queries_are_not_sent_twice():
    variants = build_search_variants(
        region="Москва", locality="Москва", address="ул. Профсоюзная, д. 123А"
    )
    keys = [" ".join(query.lower().replace(",", " ").split()) for _, query in variants]
    assert len(keys) == len(set(keys))
    assert len(variants) <= 3


def test_third_level_candidate_is_checked_against_full_original_address():
    gateway = Gateway(
        [
            row(
                region="Москва",
                locality="Москва",
                address="Профсоюзная улица, дом 123А, строение 15",
            )
        ]
    )
    broad = {
        "cadastral_number": "77:06:0003002:10",
        "address": "Москва, Профсоюзная улица, дом 123А",
    }
    searcher = Searcher([[], [], [broad]])

    stats = processor(gateway, searcher).run(1, apply=True)

    assert len(searcher.calls) == 3
    last = stats.search_diagnostics[0].attempts[-1]
    assert last.status == "all_rejected"
    assert last.rejection_reasons == {"недостаточно данных": 1}
    assert "cadastral_number" not in gateway.updates[0]["values"]


def test_multiple_verified_numbers_fill_p_without_arbitrary_j():
    gateway = Gateway([row()])
    searcher = Searcher(
        [[candidate("31:16:0101001:25"), candidate("31:16:0101001:26")]]
    )

    processor(gateway, searcher).run(1, apply=True)

    values = gateway.updates[0]["values"]
    assert "cadastral_number" not in values
    assert values["cadastral_quarter"] == "31:16:0101001"
    assert values["all_cadastral"].splitlines() == [
        "31:16:0101001:25",
        "31:16:0101001:26",
    ]


def test_diagnostics_distinguish_rejected_candidates_and_reasons():
    gateway = Gateway([row()])
    wrong = candidate(address="Белгородская область, Белгород, ул Садовая, д 6")
    stats = processor(gateway, Searcher([[wrong], [], []])).run(1)

    first = stats.search_diagnostics[0].attempts[0]
    assert first.returned == 1
    assert first.accepted == 0
    assert first.rejected == 1
    assert first.rejection_reasons == {"дом": 1}
    assert first.status == "all_rejected"
    assert first.rejected_samples == []


def test_explicit_rejection_diagnostics_show_only_first_five_candidates():
    gateway = Gateway([row()])
    wrong = [
        candidate(
            number=f"31:16:0101001:{index}",
            address=(
                "Белгородская область, Белгород, "
                f"ул Полевая, д {index}, стр {index}"
            ),
        )
        for index in range(1, 8)
    ]
    worker = CadastralEnrichmentProcessor(
        gateway,
        Searcher([wrong, []]),
        attempts=1,
        request_delay=0,
        rejection_diagnostics=True,
        sleep=lambda _seconds: None,
    )

    stats = worker.run(1)

    samples = [
        sample
        for attempt in stats.search_diagnostics[0].attempts
        for sample in attempt.rejected_samples
    ]
    assert len(samples) == 5
    first = samples[0]
    assert first.original_address.endswith("ул Полевая, д 1, стр 1")
    assert "улица полевая дом 1 строение 1" in first.normalized_candidate
    assert first.normalized_source == "белгородская белгород садовая 5"
    assert first.reason == "улица"
    assert (first.source_street, first.candidate_street) == ("садовая", "полевая")
    assert (first.source_house, first.candidate_house) == ("5", "1")
    assert (first.source_building, first.candidate_building) == ("", "1")


def test_nspd_unavailable_is_not_reported_as_empty_and_next_row_continues():
    gateway = Gateway([row(), row(row_number=3, object_number="2")])
    searcher = Searcher([ConnectionError("NSPD unavailable"), [candidate()]])

    stats = processor(gateway, searcher).run(2, apply=True)

    assert stats.checked == 2
    assert stats.errors == 1
    assert stats.search_diagnostics[0].outcome == "nspd_unavailable"
    assert any(update["object_number"] == "2" for update in gateway.updates)


def test_dry_run_searches_and_reports_plan_without_write():
    gateway = Gateway([row()])
    stats = processor(gateway, Searcher([[candidate()]])).run(1, apply=False)

    assert stats.cells_planned == 4
    assert stats.cells_updated == 0
    assert gateway.updates == []
    assert stats.search_diagnostics[0].matching_numbers == 1


def test_known_j_derives_quarter_without_address_search():
    gateway = Gateway(
        [row(address="", cadastral_number="31:16:0101001:25", all_cadastral="-", gemini="-")]
    )
    searcher = Searcher()

    processor(gateway, searcher).run(1, apply=True)

    assert gateway.updates[0]["values"] == {"cadastral_quarter": "31:16:0101001"}
    assert searcher.calls == []


def test_filter_requires_region_locality_street_house_and_qualifier():
    candidates = [
        candidate("31:16:1:1", "Белгородская область, Белгород, ул Садовая, д 5, корп 2"),
        candidate("31:16:1:2", "Белгородская область, Белгород, ул Садовая, д 5, корп 3"),
        candidate("31:16:1:3", "Курская область, Курск, ул Садовая, д 5, корп 2"),
    ]
    result = filter_candidates(
        candidates,
        region="Белгородская область",
        locality="Белгород",
        address="ул. Садовая, д. 5, корпус 2",
    )
    assert [item["cadastral_number"] for item in result] == ["31:16:1:1"]


def test_extract_candidates_supports_both_pynspd_number_fields():
    class Value:
        pass

    feature = Value()
    feature.properties = Value()
    feature.properties.options = Value()
    feature.properties.options.cad_number = None
    feature.properties.options.cad_num = "31:16:0101001:25"
    feature.properties.options.readable_address = BELGOROD_ADDRESS

    assert extract_candidates([feature])[0]["cadastral_number"] == "31:16:0101001:25"
    assert cadastral_quarter("31:16:0101001:25") == "31:16:0101001"


def test_second_run_does_not_duplicate_values_after_gateway_state_changes():
    class MutableGateway(Gateway):
        def update_object_enrichment(self, **kwargs):
            result = super().update_object_enrichment(**kwargs)
            target = self.rows[0]
            target.update(kwargs["values"])
            if kwargs["prepare_gemini"]:
                target["gemini"] = build_object_gemini_formula(target["row_number"])
            return result

    gateway = MutableGateway([row()])
    searcher = Searcher([[candidate()]])
    worker = processor(gateway, searcher)

    worker.run(1, apply=True)
    worker.run(1, apply=True)

    assert len(gateway.updates) == 1


def test_nspd_long_moscow_address_extracts_explicit_street_not_territory():
    nspd_address = (
        "Российская Федерация, г. Москва, вн.тер.г. муниципальный округ "
        "Ясенево, ул. Профсоюзная, д. 123А стр. 15"
    )

    assert _street(nspd_address) == "профсоюзная"
    assert _house(nspd_address) == "123а"
    assert _qualifier(nspd_address, "стр|строение") == "15"
    assert "муниципальный округ ясенево" != _street(nspd_address)
    assert candidate_rejection_reason(
        {"cadastral_number": "77:06:0003002:10", "address": nspd_address},
        region="Москва",
        locality="Москва",
        address="Профсоюзная улица, дом 123А, строение 15",
    ) is None


def test_street_type_after_name_and_common_abbreviations_are_supported():
    assert _street("Москва, Профсоюзная улица, дом 123А") == "профсоюзная"
    assert _street("Москва, пр-т Ленинский, дом 10") == "ленинский"
    assert _street("Москва, пер. Сивцев Вражек, дом 4") == "сивцев вражек"
    assert _street("Москва, б-р Яна Райниса, дом 2") == "яна райниса"


def test_address_without_explicit_street_type_is_not_guessed():
    assert _street("Российская Федерация, Москва, Профсоюзная, дом 123А") == ""


def test_multiple_administrative_levels_before_explicit_street_are_ignored():
    address = (
        "Российская Федерация г Москва вн тер г муниципальный округ Ясенево "
        "ул Профсоюзная д 123А стр 15"
    )

    assert _street(address) == "профсоюзная"
