"""Manual TG-08.1 entry point. Real writes require explicit ``--apply``."""

from __future__ import annotations

import argparse

from .cadastral_enrichment import (
    MAX_OBJECT_BATCH_SIZE,
    CadastralLimits,
    CadastralEnrichmentProcessor,
    PynspdCadastralSearcher,
)
from .config import Settings
from .cli import configure_logging
from .sheets import AppsScriptGateway


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Кадастровое обогащение листа ОБЪЕКТЫ через НСПД"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="найти и показать план без записи"
    )
    mode.add_argument(
        "--apply", action="store_true", help="записать только всё ещё пустые J/O/P/Q"
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="число строк 1–500 (по умолчанию 100)"
    )
    parser.add_argument("--row", type=int, help="обработать одну физическую строку листа")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=None,
        help="пауза между запросами и строками; иначе NSPD_REQUEST_DELAY_SECONDS",
    )
    parser.add_argument(
        "--nspd-attempts", type=int, default=None, help="попытки запроса НСПД 1–3"
    )
    parser.add_argument("--http-timeout", type=float, default=None)
    parser.add_argument("--row-timeout", type=float, default=None)
    parser.add_argument("--stage-timeout", type=float, default=None)
    parser.add_argument(
        "--diagnostic-rejections",
        action="store_true",
        help="показать детали первых 5 отклонённых кандидатов на строку",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_OBJECT_BATCH_SIZE:
        raise SystemExit(f"--limit должен быть от 1 до {MAX_OBJECT_BATCH_SIZE}")
    if args.row is not None and args.row < 2:
        raise SystemExit("--row должен быть не меньше 2")
    if args.request_delay is not None and args.request_delay < 0:
        raise SystemExit("--request-delay не может быть отрицательным")
    if args.nspd_attempts is not None and not 1 <= args.nspd_attempts <= 3:
        raise SystemExit("--nspd-attempts должен быть от 1 до 3")

    settings = Settings.from_env()
    limits = CadastralLimits.from_env()
    http_timeout = (
        limits.http_timeout_seconds
        if args.http_timeout is None
        else args.http_timeout
    )
    row_timeout = (
        limits.row_timeout_seconds if args.row_timeout is None else args.row_timeout
    )
    stage_timeout = (
        limits.stage_timeout_seconds
        if args.stage_timeout is None
        else args.stage_timeout
    )
    attempts = limits.attempts if args.nspd_attempts is None else args.nspd_attempts
    request_delay = (
        limits.request_delay_seconds
        if args.request_delay is None
        else args.request_delay
    )
    if min(http_timeout, row_timeout, stage_timeout) <= 0:
        raise SystemExit("тайм-ауты НСПД должны быть положительными")
    if http_timeout > row_timeout or row_timeout > stage_timeout:
        raise SystemExit("требуется HTTP timeout <= row timeout <= stage timeout")
    logger = configure_logging(str(settings.log_file)).getChild("cadastral")
    gateway = AppsScriptGateway(
        settings.apps_script_url,
        settings.apps_script_secret,
        timeout_seconds=settings.http_timeout_seconds,
        logger=logger,
    )
    processor = CadastralEnrichmentProcessor(
        gateway,
        PynspdCadastralSearcher(timeout=http_timeout),
        logger=logger,
        attempts=attempts,
        request_delay=request_delay,
        row_timeout_seconds=row_timeout,
        stage_timeout_seconds=stage_timeout,
        rejection_diagnostics=args.diagnostic_rejections,
    )
    stats = processor.run(
        args.limit, row_number=args.row, apply=args.apply
    )
    mode = "запись" if args.apply else "предпросмотр"
    summary = (
        f"TG-08.1 ({mode}): проверено={stats.checked}; "
        f"запросов НСПД={stats.nspd_queries}; "
        f"найдено номеров={stats.cadastral_numbers_found}; "
        f"запланировано ячеек={stats.cells_planned}; "
        f"обновлено строк={stats.rows_updated}; "
        f"обновлено ячеек={stats.cells_updated}; успешно строк="
        f"{stats.successful_rows}; уже было номеров="
        f"{stats.existing_numbers}; тайм-аутов={stats.timed_out_rows}; "
        f"отложено={stats.deferred_rows}; ошибок={stats.errors}"
    )
    print(summary)
    logger.info(summary)
    for diagnostic in stats.search_diagnostics:
        if not diagnostic.attempts:
            print(
                f"Строка {diagnostic.row_number}: поиск НСПД не требовался; "
                f"итог={diagnostic.outcome}"
            )
            continue
        for attempt in diagnostic.attempts:
            reasons = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(attempt.rejection_reasons.items())
            ) or "нет"
            error = f"; тип ошибки={attempt.error_type}" if attempt.error_type else ""
            print(
                f"Строка {diagnostic.row_number}; уровень={attempt.level}; "
                f"запрос={attempt.query!r}; запросов={attempt.requests}; "
                f"кандидатов НСПД={attempt.returned}; принято={attempt.accepted}; "
                f"отклонено={attempt.rejected}; причины={reasons}; "
                f"статус={attempt.status}{error}"
            )
            for sample_index, sample in enumerate(attempt.rejected_samples, start=1):
                print(
                    f"  Отклонённый кандидат {sample_index}: "
                    f"адрес НСПД={sample.original_address!r}; "
                    f"нормализованный кандидат={sample.normalized_candidate!r}; "
                    f"нормализованный источник={sample.normalized_source!r}; "
                    f"причина={sample.reason}; "
                    f"улица источник/кандидат={sample.source_street!r}/"
                    f"{sample.candidate_street!r}; "
                    f"дом источник/кандидат={sample.source_house!r}/"
                    f"{sample.candidate_house!r}; "
                    f"строение источник/кандидат={sample.source_building!r}/"
                    f"{sample.candidate_building!r}"
                )
        print(
            f"Строка {diagnostic.row_number}: итог={diagnostic.outcome}; "
            f"подходящих кадастровых номеров={diagnostic.matching_numbers}"
        )
    for error in stats.row_errors:
        print(f"Ошибка: {error}")
    if args.dry_run:
        for plan in stats.planned_rows:
            fields = "; ".join(
                f"{key}={value!r}"
                for key, value in plan.items()
                if key != "row_number"
            )
            print(f"Строка {plan['row_number']}: {fields}")
        print("Запись в Google Таблицу не выполнялась.")
    return 0 if stats.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
