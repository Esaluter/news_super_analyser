"""TG-02A: fetch one allowlisted public Telegram post without Telegram auth.

This experiment intentionally supports only the publication named below. It
does not use Telegram Bot API, a user session, Google Sheets, or browser
automation.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

import requests


POST_URL: Final = "https://t.me/operativno31/15942"
EMBED_URL: Final = POST_URL + "?embed=1&mode=tme"
OUTPUT_PATH: Final = (
    Path(__file__).resolve().parent
    / "output"
    / "telegram_operativno31_15942.txt"
)
CHECK_FRAGMENTS: Final = (
    "Информация о нанесённых ВСУ ударах",
    "посёлке Дубовое",
    "Грайворонском округе",
    "Шебекинском округе",
    "Яковлевском округе",
)
TEXT_CLASS: Final = "tgme_widget_message_text"
TIMEOUT_SECONDS: Final = 20


class TelegramTextParser(HTMLParser):
    """Extract only Telegram's server-rendered message text container."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts: list[str] = []
        self._texts: list[str] = []
        self.saw_expected_container = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if self._depth == 0 and TEXT_CLASS in classes:
            self.saw_expected_container = True
            self._depth = 1
            self._parts = []
            return
        if self._depth:
            self._depth += 1
            if tag == "br":
                self._parts.append("\n")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._depth and tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        self._depth -= 1
        if self._depth == 0:
            text = normalize_text("".join(self._parts))
            if text:
                self._texts.append(text)
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return max(self._texts, key=len, default="")


@dataclass(frozen=True)
class Attempt:
    method: str
    url: str
    text: str = ""
    error: str | None = None
    expected_structure: bool = False

    @property
    def found_fragments(self) -> tuple[str, ...]:
        return tuple(fragment for fragment in CHECK_FRAGMENTS if fragment in self.text)

    @property
    def complete(self) -> bool:
        return bool(self.text) and len(self.found_fragments) == len(CHECK_FRAGMENTS)


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    normalized: list[str] = []
    for line in lines:
        if line or (normalized and normalized[-1]):
            normalized.append(line)
    return "\n".join(normalized).strip()


def fetch_attempt(method: str, url: str) -> Attempt:
    try:
        response = requests.get(
            url,
            timeout=TIMEOUT_SECONDS,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; NewsSuperAnalyser-TG02A/1.0; "
                    "+local-experiment)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.Timeout:
        return Attempt(method, url, error=f"тайм-аут после {TIMEOUT_SECONDS} с")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "неизвестен"
        return Attempt(method, url, error=f"HTTP-ошибка, статус {status}")
    except requests.RequestException as exc:
        return Attempt(method, url, error=f"сетевая ошибка: {type(exc).__name__}")

    parser = TelegramTextParser()
    try:
        parser.feed(response.text)
        parser.close()
    except Exception as exc:
        return Attempt(
            method,
            url,
            error=f"структура HTML отличается от ожидаемой: {type(exc).__name__}",
        )

    if not parser.saw_expected_container:
        return Attempt(
            method,
            url,
            error="структура HTML отличается: контейнер текста Telegram не найден",
        )
    if not parser.text:
        return Attempt(
            method,
            url,
            error="Telegram вернул страницу с пустым контейнером публикации",
            expected_structure=True,
        )
    return Attempt(
        method,
        url,
        text=parser.text,
        expected_structure=True,
    )


def print_attempt(attempt: Attempt) -> None:
    print(f"Способ: {attempt.method}")
    if attempt.error:
        print(f"  Результат: ошибка — {attempt.error}")
        return
    print(f"  Извлечено символов: {len(attempt.text)}")
    print(
        f"  Найдено контрольных фрагментов: "
        f"{len(attempt.found_fragments)}/{len(CHECK_FRAGMENTS)}"
    )
    if not attempt.complete:
        print("  Результат: извлечён только фрагмент публикации")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    attempts = [fetch_attempt("обычная страница", POST_URL)]
    if not attempts[0].complete:
        attempts.append(fetch_attempt("Telegram embed", EMBED_URL))

    for attempt in attempts:
        print_attempt(attempt)

    best = max(attempts, key=lambda item: (len(item.found_fragments), len(item.text)))
    if best.text:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(best.text + "\n", encoding="utf-8")
        print(f"\nЛучший результат сохранён: {OUTPUT_PATH}")
        print("\n--- Извлечённый текст ---\n")
        print(best.text)
    else:
        print("\nТекст не сохранён: ни один способ не вернул содержимое публикации.")

    print("\n--- Проверка полноты ---")
    for fragment in CHECK_FRAGMENTS:
        status = "НАЙДЕН" if fragment in best.text else "НЕ НАЙДЕН"
        print(f"[{status}] {fragment}")
    print(f"Символов: {len(best.text)}")
    print(f"Фрагментов: {len(best.found_fragments)}/{len(CHECK_FRAGMENTS)}")
    print(f"Итог: {'УСПЕХ' if best.complete else 'НЕУСПЕХ'}")
    return 0 if best.complete else 1


if __name__ == "__main__":
    sys.exit(main())

