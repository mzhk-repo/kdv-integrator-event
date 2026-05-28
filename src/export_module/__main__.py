"""CLI entrypoint для batch-модуля Koha export."""

from __future__ import annotations

import sys

from src.export_module.config import (
    ConfigValidationError,
    ExportConfig,
    RuntimeOptions,
    parse_runtime_options,
)
from src.export_module.db.repository import ExportRepository


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    config = ExportConfig.from_env()

    if _has_option(args, "--health-check"):
        return _run_health_check(config)

    reset_run_id = _extract_reset_pending(args)
    if reset_run_id is not None:
        return _run_reset_pending(config, reset_run_id)

    options = parse_runtime_options(args)
    return _run_export(config, options)


def _run_export(config: ExportConfig, options: RuntimeOptions) -> int:
    from src.export_module.orchestrator import ExportOrchestrator

    return ExportOrchestrator(config).run(options)


def _run_health_check(config: ExportConfig) -> int:
    try:
        config.validate()
    except ConfigValidationError as exc:
        print(f"health_check_failed: {exc}", file=sys.stderr)
        return 2

    print("health_check_ok")
    return 0


def _run_reset_pending(config: ExportConfig, run_id: str) -> int:
    try:
        config.validate()
    except ConfigValidationError as exc:
        print(f"reset_pending_failed: {exc}", file=sys.stderr)
        return 2

    updated = ExportRepository(config.db_path).reset_stuck_pending(run_id)
    print(f"reset_pending_updated={updated}")
    return 0


def _has_option(args: list[str], option: str) -> bool:
    return option in args


def _extract_reset_pending(args: list[str]) -> str | None:
    if "--reset-pending" not in args:
        return None

    index = args.index("--reset-pending")
    try:
        run_id = args[index + 1]
    except IndexError as exc:
        raise SystemExit("--reset-pending requires RUN_ID") from exc

    if not run_id or run_id.startswith("--"):
        raise SystemExit("--reset-pending requires RUN_ID")

    unexpected = args[:index] + args[index + 2 :]
    if unexpected:
        raise SystemExit("--reset-pending cannot be combined with other options")

    return run_id


if __name__ == "__main__":
    raise SystemExit(main())
