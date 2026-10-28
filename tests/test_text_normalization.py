from __future__ import annotations

from news_super_analyser.text_normalization import normalize_publication_text


def test_removes_blank_lines_navigation_and_repeated_heading():
    text = (
        "Сводка происшествий\r\n\r\n"
        "Сводка происшествий\r\n"
        "VK | Одноклассники | MAX | Telegram\r\n"
        "Подписаться на канал\r\n"
        "Повреждены два частных дома."
    )
    assert normalize_publication_text(text) == (
        "Сводка происшествий\nПовреждены два частных дома."
    )


def test_preserves_first_meaningful_line_and_incident_details():
    text = (
        "Информация о нанесённых ударах\n"
        "В посёлке Дубовое повреждена кровля дома.\n"
        "Адрес: улица Центральная, дом 7\n"
        "Шебекинский округ — повреждён автомобиль."
    )
    assert normalize_publication_text(text) == text


def test_does_not_remove_content_merely_containing_navigation_brand():
    text = "Компания MAX сообщила о повреждении офиса.\nTelegram-канал опубликовал адрес."
    assert normalize_publication_text(text) == text


def test_collapses_inline_whitespace_without_joining_lines():
    assert normalize_publication_text("  Заголовок  \nАдрес:\t  ул. Ленина, 1  ") == (
        "Заголовок\nАдрес: ул. Ленина, 1"
    )
