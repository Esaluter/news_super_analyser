"""Manual one-pass Windows entry point for the ``КАДАСТРЫ`` queue."""

from __future__ import annotations

import argparse

from .cadastral_enrichment import CadastralLimits
from .cadastral_queue import LocalCadastralQueueProcessor
from .config import ConfigurationError, Settings
from .nspd_http import VerifiedNspdHttpSearcher
from .safe_logging import configure_safe_logging
from .sheets import AppsScriptGateway, MAX_CADASTRAL_TASK_BATCH_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Один локальный проход по листу КАДАСТРЫ"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=MAX_CADASTRAL_TASK_BATCH_SIZE,
        help="число заданий 1–500",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_CADASTRAL_TASK_BATCH_SIZE:
        raise SystemExit("--limit должен быть от 1 до 500")
    try:
        settings = Settings.from_env()
        limits = CadastralLimits.from_env()
    except (ConfigurationError, ValueError) as exc:
        print(f"Ошибка конфигурации: {exc}")
        return 2
    logger = configure_safe_logging(
        "logs/cadastral_queue_local.log",
        secrets=(settings.apps_script_secret,),
    ).getChild("cadastral_queue_local")
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logger,
    )
    searcher = VerifiedNspdHttpSearcher(
        timeout=limits.http_timeout_seconds,
        logger=logger,
    )
    stats = LocalCadastralQueueProcessor(
        gateway,
        searcher,
        limits=limits,
        logger=logger,
    ).run(args.limit)
    print(
        "КАДАСТРЫ: "
        f"всего={stats.total_records}; проверено={stats.checked}; "
        f"найдено={stats.found}; неоднозначно={stats.ambiguous}; "
        f"не найдено={stats.not_found}; ошибок={stats.errors}; "
        f"пропущено={stats.skipped}; время={stats.duration_seconds:.2f}с"
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
