"""Manual one-pass Windows entry point for the ``КАДАСТРЫ`` queue."""

from __future__ import annotations

import argparse

from .cadastral_enrichment import CadastralLimits
from .cadastral_queue import LocalCadastralQueueProcessor
from .config import ConfigurationError, PROJECT_ROOT, Settings
from .nspd_debug import NspdDebugRecorder
from .nspd_http import VerifiedNspdHttpSearcher
from .safe_logging import configure_safe_logging
from .sheets import AppsScriptError, AppsScriptGateway, MAX_CADASTRAL_TASK_BATCH_SIZE


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
    parser.add_argument(
        "--debug-nspd",
        action="store_true",
        help="сохранить исходные HTTP-ответы НСПД локально",
    )
    parser.add_argument(
        "--object-id",
        help="диагностировать один составной ID без записи в Google Sheets",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_CADASTRAL_TASK_BATCH_SIZE:
        parser.error("--limit должен быть от 1 до 500")
    if args.object_id is not None and not args.debug_nspd:
        parser.error("--object-id доступен только вместе с --debug-nspd")
    if args.object_id is not None:
        args.object_id = args.object_id.strip()
        if not args.object_id:
            parser.error("--object-id не может быть пустым")
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
    recorder = (
        NspdDebugRecorder(
            PROJECT_ROOT / "logs" / "nspd_debug",
            mode="single_object" if args.object_id is not None else "queue",
            logger=logger,
        )
        if args.debug_nspd
        else None
    )
    searcher = VerifiedNspdHttpSearcher(
        timeout=limits.http_timeout_seconds,
        logger=logger,
        debug_recorder=recorder,
    )
    processor = LocalCadastralQueueProcessor(
        gateway,
        searcher,
        limits=limits,
        logger=logger,
        diagnostics=recorder,
    )
    try:
        if args.object_id is not None:
            return _diagnose_single_object(
                args.object_id, gateway, processor, recorder
            )
        stats = processor.run(args.limit)
        print(
            "КАДАСТРЫ: "
            f"всего={stats.total_records}; проверено={stats.checked}; "
            f"найдено={stats.found}; неоднозначно={stats.ambiguous}; "
            f"не найдено={stats.not_found}; ошибок={stats.errors}; "
            f"пропущено={stats.skipped}; время={stats.duration_seconds:.2f}с"
        )
        return 1 if stats.errors else 0
    finally:
        if recorder is not None:
            directory = recorder.finish()
            if directory is not None:
                print(f"Диагностика НСПД сохранена: {directory}")
            else:
                print("Не удалось создать каталог диагностики НСПД")


def _diagnose_single_object(
    object_id: str,
    gateway: AppsScriptGateway,
    processor: LocalCadastralQueueProcessor,
    recorder: NspdDebugRecorder | None,
) -> int:
    try:
        task = gateway.read_cadastral_task(object_id)
    except AppsScriptError as exc:
        if recorder is not None:
            recorder.record_lookup_error(object_id, type(exc).__name__)
        print(f"Не удалось прочитать объект {object_id}: {exc}")
        return 2
    if task is None:
        if recorder is not None:
            recorder.record_lookup_error(object_id, "not_found")
        print(f"Объект с ID {object_id} не найден в листе КАДАСТРЫ")
        return 2
    evaluation = processor.diagnose_task(task)
    print(
        f"Диагностика объекта {object_id} завершена; "
        f"исходный статус={task['status']}; результат={evaluation.status}; "
        "Google Sheets не изменялись"
    )
    return 1 if evaluation.status == "ОШИБКА" else 0


if __name__ == "__main__":
    raise SystemExit(main())
