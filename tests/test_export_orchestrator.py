import logging
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.config import ExportConfig, RuntimeOptions  # noqa: E402
from src.export_module.db.repository import ExportRepository  # noqa: E402
from src.export_module.orchestrator import ExportOrchestrator  # noqa: E402
from src.export_module.services.drive_mount_service import DriveMountCopyResult  # noqa: E402
from src.export_module.services.graph_email_service import GraphEmailSendResult  # noqa: E402


class _Config(ExportConfig):
    def validate(self, **_kwargs) -> None:
        return None


class _KohaClient:
    def __init__(self, biblios=None, marcxml_by_id=None):
        self.biblios = biblios or [{"biblionumber": 101}]
        self.marcxml_by_id = marcxml_by_id or {101: "<record />"}

    def fetch_all_biblios_keyset(self, **kwargs):
        return iter(self.biblios)

    def fetch_biblio_marcxml(self, biblionumber):
        return self.marcxml_by_id[biblionumber]


class _MarcParser:
    def parse_record(self, marcxml):
        return {"ID Запису": "101", "Назва книги": "Тест", "Тип документа": "Книга"}

    def has_file_link(self, marcxml):
        return "file-link" in marcxml or marcxml == "<record />"


class _XLSXGenerator:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.last_path = None

    def generate(self, records, run_id):
        path = self.tmp_path / f"export_Koha_2026-05-28_120000_{run_id[:8]}.xlsx"
        path.write_bytes(b"xlsx")
        self.last_path = path
        return str(path)


class _DriveService:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []

    def copy_to_mount(self, xlsx_path, run_id):
        self.calls.append((xlsx_path, run_id))
        if self.should_fail:
            raise OSError("drive copy failed")
        return DriveMountCopyResult(
            file_path=str(Path(xlsx_path)),
            folder_path=str(Path(xlsx_path).parent),
            file_name=Path(xlsx_path).name,
        )


class _GraphService:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []

    def send_via_graph(self, records, drive_result, xlsx_path, run_id):
        self.calls.append((records, drive_result, xlsx_path, run_id))
        if self.should_fail:
            raise RuntimeError("graph failed")
        return GraphEmailSendResult(
            recipient="target@example.org",
            attachment_included=True,
            attachment_size_bytes=4,
            message_id="message-1",
        )


def _config(tmp_path):
    return _Config(
        enabled=True,
        koha_base_url="https://koha.example.org",
        koha_api_user="koha-user",
        koha_api_password="koha-pass",
        export_gdrive_root_path="/mnt/drive/KohaExports",
        graph_tenant_id="tenant-id",
        graph_client_id="client-id",
        graph_client_secret="secret",
        graph_sender_user_id="sender@example.org",
        graph_to="target@example.org",
        db_path=str(tmp_path / "export_state.db"),
        marc_mapping_path="config/marc_mapping.yaml",
        export_dictionaries_path="config/export_dictionaries.yaml",
    )


def _rows(repo):
    with sqlite3.connect(repo.db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT *
            FROM exported_records
            ORDER BY biblionumber ASC, run_id ASC
            """
        ).fetchall()


def _orchestrator(
    tmp_path, repository=None, drive=None, graph=None, xlsx=None, koha=None
):
    config = _config(tmp_path)
    repo = repository or ExportRepository(config.db_path)
    xlsx_generator = xlsx or _XLSXGenerator(tmp_path)
    return ExportOrchestrator(
        config=config,
        repository=repo,
        koha_client=koha or _KohaClient(),
        marc_parser=_MarcParser(),
        xlsx_generator=xlsx_generator,
        drive_mount_service=drive or _DriveService(),
        graph_email_service=graph or _GraphService(),
        run_id_factory=lambda: "run12345-0000-0000-0000-000000000000",
    ), repo, xlsx_generator


def test_happy_path_marks_records_completed(tmp_path):
    orchestrator, repo, xlsx_generator = _orchestrator(tmp_path)

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert len(rows) == 1
    assert rows[0]["biblionumber"] == 101
    assert rows[0]["status"] == "completed"
    assert rows[0]["email_message_id"] == "message-1"
    assert not xlsx_generator.last_path.exists()


def test_manual_export_copies_only_file_links_without_state_or_email(tmp_path):
    koha = _KohaClient(
        biblios=[{"biblionumber": 101}, {"biblionumber": 202}],
        marcxml_by_id={101: "file-link", 202: "no-link"},
    )
    graph = _GraphService()
    drive = _DriveService()
    orchestrator, repo, _ = _orchestrator(
        tmp_path, koha=koha, graph=graph, drive=drive
    )

    assert orchestrator.run(
        RuntimeOptions(
            biblionumber_from=100,
            biblionumber_to=150,
            export_mode="file-links",
            manual_export=True,
        )
    ) == 0

    assert _rows(repo) == []
    assert graph.calls == []
    assert len(drive.calls) == 1
    assert orchestrator.last_export_path == str(
        tmp_path / Path(drive.calls[0][0]).name
    )


def test_biblio_id_payload_is_exported_successfully(tmp_path):
    koha = _KohaClient(biblios=[{"biblio_id": 202}], marcxml_by_id={202: "<record />"})
    orchestrator, repo, _ = _orchestrator(tmp_path, koha=koha)

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert len(rows) == 1
    assert rows[0]["biblionumber"] == 202
    assert rows[0]["status"] == "completed"


def test_default_all_mode_is_stateless(tmp_path, caplog):
    drive = _DriveService()
    graph = _GraphService()
    orchestrator, repo, _ = _orchestrator(tmp_path, drive=drive, graph=graph)

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions()) == 0

    assert _rows(repo) == []
    assert len(drive.calls) == 1
    assert len(graph.calls) == 1
    log_events = {record.getMessage() for record in caplog.records}
    assert "stateless_mode" in log_events
    assert "db_not_modified" in log_events


def test_file_links_mode_initializes_lazy_repository(tmp_path):
    config = _config(tmp_path)
    orchestrator = ExportOrchestrator(
        config=config,
        repository=None,
        koha_client=_KohaClient(),
        marc_parser=_MarcParser(),
        xlsx_generator=_XLSXGenerator(tmp_path),
        drive_mount_service=_DriveService(),
        graph_email_service=_GraphService(),
        run_id_factory=lambda: "run12345-0000-0000-0000-000000000000",
    )

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    assert orchestrator.repository is not None
    rows = _rows(orchestrator.repository)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_file_links_mode_exports_only_856_file_records(tmp_path, caplog):
    koha = _KohaClient(
        biblios=[{"biblionumber": 101}, {"biblionumber": 102}],
        marcxml_by_id={101: "file-link", 102: "handle-only"},
    )
    orchestrator, repo, _ = _orchestrator(tmp_path, koha=koha)

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert len(rows) == 1
    assert rows[0]["biblionumber"] == 101
    assert rows[0]["status"] == "completed"
    filter_log = next(
        record for record in caplog.records
        if record.getMessage() == "marc_file_link_filter_completed"
    )
    assert filter_log.checked == 2
    assert filter_log.matched == 1
    assert filter_log.skipped == 1


def test_drive_copy_failure_marks_failed_and_does_not_call_graph(tmp_path):
    graph = _GraphService()
    orchestrator, repo, xlsx_generator = _orchestrator(
        tmp_path, drive=_DriveService(should_fail=True), graph=graph
    )

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 2

    rows = _rows(repo)
    assert rows[0]["status"] == "failed"
    assert "drive copy failed" in rows[0]["failed_reason"]
    assert graph.calls == []
    assert not xlsx_generator.last_path.exists()


def test_graph_failure_after_copy_preserves_gdrive_uploaded(tmp_path, caplog):
    graph = _GraphService(should_fail=True)
    orchestrator, repo, xlsx_generator = _orchestrator(tmp_path, graph=graph)

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 2

    rows = _rows(repo)
    assert rows[0]["status"] == "gdrive_uploaded"
    assert rows[0]["gdrive_file_path"] is not None
    assert not xlsx_generator.last_path.exists()

    log_events = {record.getMessage() for record in caplog.records}
    assert "gdrive_uploaded" in log_events
    assert "graph_email_started" in log_events
    assert "export_failed" in log_events
    failed_log = next(record for record in caplog.records if record.getMessage() == "export_failed")
    assert failed_log.stage == "graph_email"
    assert failed_log.gdrive_file_path is not None


def test_recovery_after_email_sent_marks_completed_without_resending_email(tmp_path):
    config = _config(tmp_path)
    repo = ExportRepository(config.db_path)
    repo.insert_pending([101], "recover-run")
    repo.mark_xlsx_generated("recover-run", "export.xlsx")
    repo.mark_gdrive_uploaded("recover-run", "/mnt/drive/export.xlsx", "/mnt/drive")
    repo.mark_email_sent("recover-run", "message-1")
    graph = _GraphService()
    orchestrator, _, _ = _orchestrator(tmp_path, repository=repo, graph=graph)

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert rows[0]["status"] == "completed"
    assert graph.calls == []


def test_recovery_from_gdrive_uploaded_continues_with_email_stage(tmp_path):
    config = _config(tmp_path)
    repo = ExportRepository(config.db_path)
    repo.insert_pending([101], "recover-run")
    repo.mark_xlsx_generated("recover-run", "export.xlsx")
    gdrive_file = tmp_path / "export.xlsx"
    gdrive_file.write_bytes(b"xlsx")
    repo.mark_gdrive_uploaded(
        "recover-run", str(gdrive_file), str(tmp_path)
    )
    graph = _GraphService()
    orchestrator, _, _ = _orchestrator(tmp_path, repository=repo, graph=graph)

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert rows[0]["status"] == "completed"
    assert rows[0]["email_message_id"] == "message-1"
    assert len(graph.calls) == 1
    assert graph.calls[0][3] == "recover-run"


def test_recovery_from_xlsx_generated_continues_with_drive_and_email(tmp_path):
    config = _config(tmp_path)
    repo = ExportRepository(config.db_path)
    repo.insert_pending([101], "recover-run")
    xlsx_file = tmp_path / "export.xlsx"
    xlsx_file.write_bytes(b"xlsx")
    repo.mark_xlsx_generated("recover-run", str(xlsx_file))
    drive = _DriveService()
    graph = _GraphService()
    orchestrator, _, _ = _orchestrator(
        tmp_path, repository=repo, drive=drive, graph=graph
    )

    assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert rows[0]["status"] == "completed"
    assert rows[0]["gdrive_file_path"] is not None
    assert rows[0]["email_message_id"] == "message-1"
    assert drive.calls == [(str(tmp_path / "export.xlsx"), "recover-run")]
    assert len(graph.calls) == 1
    assert graph.calls[0][3] == "recover-run"


def test_recovery_missing_gdrive_file_marks_failed_and_continues_export(tmp_path, caplog):
    config = _config(tmp_path)
    repo = ExportRepository(config.db_path)
    repo.insert_pending([101], "recover-run")
    repo.mark_xlsx_generated("recover-run", "export.xlsx")
    repo.mark_gdrive_uploaded(
        "recover-run", str(tmp_path / "missing.xlsx"), str(tmp_path)
    )
    graph = _GraphService()
    orchestrator, _, _ = _orchestrator(tmp_path, repository=repo, graph=graph)

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert [(row["run_id"], row["status"]) for row in rows] == [
        ("recover-run", "failed"),
        ("run12345-0000-0000-0000-000000000000", "completed"),
    ]
    assert "recovery_missing_artifact" in rows[0]["failed_reason"]
    assert {record.getMessage() for record in caplog.records} >= {
        "recovery_missing_artifact",
        "export_completed",
    }
    assert len(graph.calls) == 1
    assert graph.calls[0][3] == "run12345-0000-0000-0000-000000000000"


def test_recovery_missing_xlsx_file_marks_failed_and_continues_export(tmp_path, caplog):
    config = _config(tmp_path)
    repo = ExportRepository(config.db_path)
    repo.insert_pending([101], "recover-run")
    repo.mark_xlsx_generated("recover-run", str(tmp_path / "missing.xlsx"))
    drive = _DriveService()
    orchestrator, _, _ = _orchestrator(tmp_path, repository=repo, drive=drive)

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions(export_mode="file-links")) == 0

    rows = _rows(repo)
    assert [(row["run_id"], row["status"]) for row in rows] == [
        ("recover-run", "failed"),
        ("run12345-0000-0000-0000-000000000000", "completed"),
    ]
    assert "recovery_missing_artifact" in rows[0]["failed_reason"]
    assert {record.getMessage() for record in caplog.records} >= {
        "recovery_missing_artifact",
        "export_completed",
    }
    assert drive.calls == [(str(tmp_path / "export_Koha_2026-05-28_120000_run12345.xlsx"), "run12345-0000-0000-0000-000000000000")]


def test_dry_run_does_not_write_db_or_call_side_effects(tmp_path, caplog):
    drive = _DriveService()
    graph = _GraphService()
    orchestrator, repo, xlsx_generator = _orchestrator(
        tmp_path, drive=drive, graph=graph
    )

    with caplog.at_level(logging.INFO):
        assert orchestrator.run(RuntimeOptions(dry_run=True, export_mode="file-links")) == 0

    assert _rows(repo) == []
    assert drive.calls == []
    assert graph.calls == []
    assert not xlsx_generator.last_path.exists()
    dry_run_file = Path(os.path.join("/tmp", "dry_run", xlsx_generator.last_path.name))
    assert dry_run_file.exists()
    dry_run_file.unlink()

    log_events = {record.getMessage() for record in caplog.records}
    assert "would_copy_to_gdrive_mount" in log_events
    assert "would_send_graph_email" in log_events
    assert "db_not_modified" in log_events
