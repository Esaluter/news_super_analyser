"""Conservative cleanup of publication text before AI analysis."""

from __future__ import annotations

import re


_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_INLINE_SPACE = re.compile(r"[ \t\v\f]+")
_NAV_SEPARATOR = re.compile(r"\s*(?:\||•|·|—|–)\s*")
_NAV_LABELS = {
    "vk",
    "вконтакте",
    "одноклассники",
    "ok",
    "max",
    "telegram",
    "телеграм",
}
_TECHNICAL_LINE_PATTERNS = (
    re.compile(r"^(?:подписаться|подпишитесь)(?:\s+на\s+(?:канал|нас))?[.!]?$", re.I),
    re.compile(r"^(?:читать|смотреть)\s+(?:далее|полностью)[.!]?$", re.I),
    re.compile(
        r"^(?:предыдущая|следующая|назад)"
        r"(?:\s+(?:новость|публикация))?[.!]?$",
        re.I,
    ),
)


def normalize_publication_text(value: str) -> str:
    """Remove known UI noise while preserving factual publication content.

    The function deliberately avoids broad keyword filtering. A line is removed
    only when its whole value matches a known navigation/signature form, or when
    it is a repeated heading-like line.
    """
    text = _ZERO_WIDTH.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    seen_headings: set[str] = set()

    for raw_line in text.split("\n"):
        line = _INLINE_SPACE.sub(" ", raw_line).strip()
        if not line or _is_navigation_line(line) or _is_technical_line(line):
            continue
        identity = line.casefold()
        if _is_heading_like(line):
            if identity in seen_headings:
                continue
            seen_headings.add(identity)
        result.append(line)

    return "\n".join(result)


def _is_navigation_line(line: str) -> bool:
    parts = [part.strip().casefold() for part in _NAV_SEPARATOR.split(line)]
    return len(parts) >= 2 and all(part in _NAV_LABELS for part in parts)


def _is_technical_line(line: str) -> bool:
    return any(pattern.fullmatch(line) for pattern in _TECHNICAL_LINE_PATTERNS)


def _is_heading_like(line: str) -> bool:
    return len(line) <= 140 and not re.search(r"[.!?;:]", line)
