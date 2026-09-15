"""Стадійна orchestration pipeline для Koha export."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from src.export_module.config import (
    EXPORT_MODE_FILE_LINKS,
    ExportConfig,
    RuntimeOptions,
)
from src.export_module.db.repository import ExportRecord, ExportRepository
from src.export_module.koha.client import KohaApiClient, KohaApiClientError
from src.export_module.koha.filters import filter_exportable_biblios
from src.export_module.marc.mapping_loader import MappingLoader
from src.export_module.marc.parser import MARCParser
from src.export_module.observability.logger import (
    get_export_logger,
    reset_run_id,
    set_run_id,
)
from src.export_module.services.drive_mount_service import (
    DriveMountCopyResult,
    ExportDriveMountService,
)
from src.export_module.services.graph_email_service import GraphEmailService
from src.export_module.xlsx.generator import XLSXGenerator

LOGGER = get_export_logger(__name__)


class ExportOrchestrator:
    def __init__(
        self,
        config: ExportConfig,
        repository: ExportRepository | None = None,
        koha_client: KohaApiClient | None = None,
        marc_parser: MARCParser | None = None,
        xlsx_generator: XLSXGenerator | None = None,
        drive_mount_service: ExportDriveMountService | None = None,
        graph_email_service: GraphEmailService | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.koha_client = koha_client or KohaApiClient(
            config.koha_base_url,
            config.koha_api_user,
            config.koha_api_password,
            page_size=config.koha_page_size,
        )
        mapping = None
        if marc_parser is None or xlsx_generator is None:
            mapping = MappingLoader(
                config.marc_mapping_path, config.export_dictionaries_path
            ).load()

        if marc_parser is None:
            if mapping is None:
                raise RuntimeError("MARC mapping was not initialized")
            self.marc_parser = MARCParser(mapping)
        else:
            self.marc_parser = marc_parser

        if xlsx_generator is None:
            if mapping is None:
                raise RuntimeError("MARC mapping was not initialized")
            self.xlsx_generator = XLSXGenerator(mapping)
        else:
            self.xlsx_generator = xlsx_generator
        self.drive_mount_service = drive_mount_service or ExportDriveMountService(
            config.export_gdrive_root_path
        )
        self.graph_email_service = graph_email_service or GraphEmailService(config)
        self.run_id_factory = run_id_factory or (lambda: str(uuid4()))
        self.last_export_path: str | None = None

    def run(self, options: RuntimeOptions) -> int:
        run_id = self.run_id_factory()
        run_token = set_run_id(run_id)
        xlsx_path: str | None = None
        drive_result: DriveMountCopyResult | None = None
        preserve_staged_state_on_failure = False
        stateful = (
            options.export_mode == EXPORT_MODE_FILE_LINKS and not options.manual_export
        )
        require_file_link = options.export_mode == EXPORT_MODE_FILE_LINKS
        stage = "start"
        records_count = 0
        candidates_count = 0
        exportable_count = 0
        biblionumber_from = getattr(options, "biblionumber_from", None)
        biblionumber_to = getattr(options, "biblionumber_to", None)
        try:
            LOGGER.info(
                "export_started",
                extra={
                    "dry_run": options.dry_run,
                    "export_mode": options.export_mode,
                    "manual_export": options.manual_export,
                    "biblionumber_from": biblionumber_from,
                    "biblionumber_to": biblionumber_to,
                },
            )
            stage = "config_validation"
            self.config.validate(
                require_graph=(
                    not options.manual_export
                    or (options.send_email and not options.dry_run)
                ),
                prepare_state=stateful,
            )
            LOGGER.info("config_validated")

            if stateful:
                stage = "recovery_check"
                if self._recover_staged_runs():
                    LOGGER.info("export_recovery_completed")
                    return 0
            else:
                LOGGER.info("stateless_mode", extra={"db_not_modified": True})

            stage = "koha_fetch"
            LOGGER.info(
                "koha_fetch_started",
                extra={
                    "biblionumber_from": biblionumber_from,
                    "biblionumber_to": biblionumber_to,
                },
            )
            candidates = list(
                self.koha_client.fetch_all_biblios_keyset(
                    biblionumber_from=biblionumber_from,
                    biblionumber_to=biblionumber_to,
                )
            )
            candidates_count = len(candidates)
            LOGGER.info(
                "koha_candidates_fetched", extra={"candidates": candidates_count}
            )

            stage = "filter_exportable"
            if stateful:
                exportable = filter_exportable_biblios(
                    candidates,
                    self._repository(),
                    self.config.max_retries,
                    biblionumber_from=biblionumber_from,
                    biblionumber_to=biblionumber_to,
                )
            else:
                exportable = [dict(candidate) for candidate in candidates]
            exportable_count = len(exportable)
            LOGGER.info(
                "exportable_filtered",
                extra={
                    "candidates": candidates_count,
                    "exportable": exportable_count,
                    "stateful": stateful,
                },
            )
            if not exportable:
                LOGGER.info("export_no_candidates")
                return 0

            stage = "marc_parsing"
            LOGGER.info("marc_parsing_started", extra={"records": exportable_count})
            selected_biblios, records, file_link_skipped, parse_skipped = self._parse_records(
                exportable, require_file_link=require_file_link
            )
            records_count = len(records)
            if stateful:
                LOGGER.info(
                    "marc_file_link_filter_completed",
                    extra={
                        "checked": exportable_count,
                        "matched": len(selected_biblios),
                        "skipped": file_link_skipped,
                    },
                )
            LOGGER.info(
                "marc_parsing_completed",
                extra={"records": records_count, "skipped": file_link_skipped + parse_skipped},
            )
            if not records:
                LOGGER.info("export_no_candidates")
                return 0

            stage = "xlsx_generation"
            xlsx_path = self.xlsx_generator.generate(records, run_id)
            LOGGER.info(
                "xlsx_generated",
                extra={"records": records_count, "xlsx_path": xlsx_path},
            )

            if options.dry_run:
                stage = "dry_run_preserve"
                dry_path = self._preserve_dry_run_copy(xlsx_path)
                LOGGER.info("would_copy_to_gdrive_mount", extra={"xlsx_path": dry_path})
                LOGGER.info("would_send_graph_email", extra={"records": len(records)})
                LOGGER.info("db_not_modified")
                LOGGER.info("export_dry_run", extra={"xlsx_path": dry_path})
                return 0

            if stateful:
                stage = "pending_reservation"
                biblionumbers = [_extract_biblionumber(record) for record in selected_biblios]
                self._repository().insert_pending(biblionumbers, run_id)
                LOGGER.info(
                    "pending_reserved",
                    extra={"records": len(biblionumbers), "status": "pending"},
                )
                self._repository().mark_xlsx_generated(run_id, Path(xlsx_path).name)
                LOGGER.info(
                    "state_xlsx_generated",
                    extra={"xlsx_filename": Path(xlsx_path).name, "status": "xlsx_generated"},
                )
            else:
                LOGGER.info("db_not_modified", extra={"export_mode": options.export_mode})

            stage = "gdrive_copy"
            LOGGER.info("gdrive_copy_started", extra={"xlsx_path": xlsx_path})
            drive_result = self.drive_mount_service.copy_to_mount(xlsx_path, run_id)
            self.last_export_path = drive_result.file_path
            if stateful:
                self._repository().mark_gdrive_uploaded(
                    run_id, drive_result.file_path, drive_result.folder_path
                )
            LOGGER.info(
                "gdrive_uploaded",
                extra={
                    "gdrive_file_path": drive_result.file_path,
                    "gdrive_folder_path": drive_result.folder_path,
                    "gdrive_skipped_existing": drive_result.was_skipped,
                    "status": "gdrive_uploaded" if stateful else "stateless",
                },
            )
            preserve_staged_state_on_failure = stateful

            if options.manual_export and not options.send_email:
                LOGGER.info(
                    "export_completed",
                    extra={
                        "records_exported": len(records),
                        "gdrive_file_path": drive_result.file_path,
                        "status": "manual",
                    },
                )
                return 0

            stage = "graph_email"
            LOGGER.info(
                "graph_email_started",
                extra={"records": records_count, "gdrive_file_path": drive_result.file_path},
            )
            email_result = self.graph_email_service.send_via_graph(
                records, drive_result, xlsx_path, run_id
            )
            if stateful:
                self._repository().mark_email_sent(run_id, email_result.message_id)
            LOGGER.info(
                "graph_email_sent",
                extra={"message_id": email_result.message_id, "status": "email_sent" if stateful else "stateless"},
            )
            if stateful:
                self._repository().mark_completed(run_id)
            LOGGER.info(
                "export_completed",
                extra={"records_exported": len(records), "status": "completed" if stateful else "stateless"},
            )
            return 0
        except Exception as exc:
            LOGGER.error(
                "export_failed",
                extra={
                    "error": str(exc),
                    "stage": stage,
                    "candidates": candidates_count,
                    "exportable": exportable_count,
                    "records": records_count,
                    "xlsx_path": xlsx_path,
                    "gdrive_file_path": drive_result.file_path if drive_result else None,
                },
            )
            if stateful and not preserve_staged_state_on_failure:
                self._repository().mark_failed(run_id, str(exc))
            return 2
        finally:
            if xlsx_path and os.path.exists(xlsx_path):
                os.unlink(xlsx_path)
            reset_run_id(run_token)

    def _repository(self) -> ExportRepository:
        if self.repository is None:
            self.repository = ExportRepository(self.config.db_path)
        return self.repository

    def _recover_staged_runs(self) -> bool:
        repository = self._repository()
        recoverable = repository.get_recoverable_runs()
        if not recoverable:
            return False

        recovered_any = False
        by_run_id: dict[str, list[ExportRecord]] = {}
        for record in recoverable:
            by_run_id.setdefault(record.run_id, []).append(record)

        for run_id, records in by_run_id.items():
            statuses = {record.status for record in records}
            if "email_sent" in statuses:
                repository.mark_completed(run_id)
                LOGGER.info("export_recovered_completed", extra={"run_id": run_id})
                recovered_any = True
                continue

            if "gdrive_uploaded" in statuses:
                record = _first_record_with_status(records, "gdrive_uploaded")
                if not _existing_path(record.gdrive_file_path):
                    self._mark_recovery_missing_artifact(
                        run_id=run_id,
                        status="gdrive_uploaded",
                        path=record.gdrive_file_path,
                    )
                    continue
                drive_result = _drive_result_from_record(record)
                email_result = self.graph_email_service.send_via_graph(
                    [], drive_result, record.gdrive_file_path or "", run_id
                )
                repository.mark_email_sent(run_id, email_result.message_id)
                repository.mark_completed(run_id)
                LOGGER.info("export_recovered_email", extra={"run_id": run_id})
                recovered_any = True
                continue

            if "xlsx_generated" in statuses:
                record = _first_record_with_status(records, "xlsx_generated")
                xlsx_path = record.xlsx_filename or ""
                if not _existing_path(xlsx_path):
                    self._mark_recovery_missing_artifact(
                        run_id=run_id,
                        status="xlsx_generated",
                        path=xlsx_path,
                    )
                    continue
                drive_result = self.drive_mount_service.copy_to_mount(xlsx_path, run_id)
                repository.mark_gdrive_uploaded(
                    run_id, drive_result.file_path, drive_result.folder_path
                )
                email_result = self.graph_email_service.send_via_graph(
                    [], drive_result, xlsx_path, run_id
                )
                repository.mark_email_sent(run_id, email_result.message_id)
                repository.mark_completed(run_id)
                LOGGER.info("export_recovered_drive", extra={"run_id": run_id})
                recovered_any = True

        return recovered_any

    def _mark_recovery_missing_artifact(
        self, run_id: str, status: str, path: str | None
    ) -> None:
        reason = f"recovery_missing_artifact:{status}:{path or 'missing_path'}"
        LOGGER.warning(
            "recovery_missing_artifact",
            extra={"run_id": run_id, "status": status, "path": path},
        )
        self._repository().mark_failed(run_id, reason)

    def _parse_records(
        self, biblios: Iterable[dict], require_file_link: bool = False
    ) -> tuple[list[dict], list[dict[str, str | None]], int, int]:
        selected_biblios: list[dict] = []
        records: list[dict[str, str | None]] = []
        file_link_skipped = 0
        parse_skipped = 0
        for biblio in biblios:
            biblionumber = _extract_biblionumber(biblio)
            marcxml = self.koha_client.fetch_biblio_marcxml(biblionumber)
            if require_file_link and not self.marc_parser.has_file_link(marcxml):
                file_link_skipped += 1
                continue
            parsed = self.marc_parser.parse_record(marcxml)
            if parsed is not None:
                selected_biblios.append(dict(biblio))
                records.append(parsed)
            else:
                parse_skipped += 1
        return selected_biblios, records, file_link_skipped, parse_skipped

    @staticmethod
    def _preserve_dry_run_copy(xlsx_path: str) -> str:
        dry_run_dir = Path(gettempdir()) / "dry_run"
        dry_run_dir.mkdir(parents=True, exist_ok=True)
        dry_path = dry_run_dir / Path(xlsx_path).name
        shutil.copy2(xlsx_path, dry_path)
        return str(dry_path)


def _extract_biblionumber(record: dict) -> int:
    try:
        return int(record.get("biblionumber") or record["biblio_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KohaApiClientError("Koha biblio item has invalid biblionumber") from exc


def _existing_path(path: str | None) -> bool:
    return bool(path and Path(path).is_file())


def _first_record_with_status(records: list[ExportRecord], status: str) -> ExportRecord:
    for record in records:
        if record.status == status:
            return record
    raise RuntimeError(f"Recoverable run does not include status: {status}")


def _drive_result_from_record(record: ExportRecord) -> DriveMountCopyResult:
    if not record.gdrive_file_path or not record.gdrive_folder_path:
        raise RuntimeError("Recoverable gdrive_uploaded record has no drive path")
    return DriveMountCopyResult(
        file_path=record.gdrive_file_path,
        folder_path=record.gdrive_folder_path,
        file_name=Path(record.gdrive_file_path).name,
        was_skipped=True,
    )
