"""Central logging that keeps application detail while redacting secrets."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


_AUTH_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_BOT_URL_RE = re.compile(r"https://api\.telegram\.org/bot[^/\s]+", re.I)


class SecretRedactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self.secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def redact(self, value: object) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _AUTH_RE.sub("[REDACTED]", text)
        return _BOT_URL_RE.sub("https://api.telegram.org/bot[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redactor.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.redactor.redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    self.redactor.redact(v) if isinstance(v, str) else v
                    for v in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, redactor: SecretRedactor) -> None:
        super().__init__(fmt)
        self.redactor = redactor

    def formatException(self, ei: object) -> str:
        return self.redactor.redact(super().formatException(ei))


def configure_safe_logging(log_file: str, *, secrets: Iterable[str] = ()) -> logging.Logger:
    logger = logging.getLogger("news_super_analyser")
    logger.setLevel(logging.INFO)
    logger.propagate = True
    logger.handlers.clear()
    redactor = SecretRedactor(secrets)
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(message)s", redactor)
    log_filter = RedactingFilter(redactor)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(log_filter)
    logger.addHandler(stream)

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    rotating = RotatingFileHandler(
        path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    rotating.addFilter(log_filter)
    logger.addHandler(rotating)

    for noisy in ("httpx", "httpcore", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logger
