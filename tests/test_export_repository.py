import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.db.repository import ExportRepository  # noqa: E402


def _repo(tmp_path):
    return ExportRepository(str(tmp_path / "export_state.db"))


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


def _status_by_run(repo):
    return {(row["biblionumber"], row["run_id"]): row["status"] for row in _rows(repo)}


def test_insert_pending_is_idempotent_for_same_run_id(tmp_path):
    repo = _repo(tmp_path)

    repo.insert_pending([101, 102], "run-1")
    repo.insert_pending([101, 102], "run-1")

    rows = _rows(repo)

    assert len(rows) == 2
    assert {(row["biblionumber"], row["run_id"]) for row in rows} == {
        (101, "run-1"),
        (102, "run-1"),
    }
    assert {row["status"] for row in rows} == {"pending"}


def test_mark_gdrive_uploaded_updates_only_target_run_id(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-1")
    repo.insert_pending([101], "run-2")
    repo.mark_xlsx_generated("run-1", "export-1.xlsx")
    repo.mark_xlsx_generated("run-2", "export-2.xlsx")

    repo.mark_gdrive_uploaded("run-1", "/mnt/drive/export-1.xlsx", "/mnt/drive")

    statuses = _status_by_run(repo)
    assert statuses[(101, "run-1")] == "gdrive_uploaded"
    assert statuses[(101, "run-2")] == "xlsx_generated"

    rows = {
        (row["biblionumber"], row["run_id"]): row
        for row in _rows(repo)
    }
    assert rows[(101, "run-1")]["gdrive_file_path"] == "/mnt/drive/export-1.xlsx"
    assert rows[(101, "run-2")]["gdrive_file_path"] is None


def test_mark_completed_only_updates_email_sent_records(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101, 102, 103], "run-1")
    repo.mark_xlsx_generated("run-1", "export.xlsx")
    repo.mark_gdrive_uploaded("run-1", "/mnt/drive/export.xlsx", "/mnt/drive")
    repo.mark_email_sent("run-1", "message-1")

    with sqlite3.connect(repo.db_path) as connection:
        connection.execute(
            """
            UPDATE exported_records
            SET status = 'gdrive_uploaded'
            WHERE biblionumber = 102
            """
        )
        connection.execute(
            """
            UPDATE exported_records
            SET status = 'failed'
            WHERE biblionumber = 103
            """
        )

    repo.mark_completed("run-1")

    statuses = _status_by_run(repo)
    assert statuses[(101, "run-1")] == "completed"
    assert statuses[(102, "run-1")] == "gdrive_uploaded"
    assert statuses[(103, "run-1")] == "failed"
    assert repo.get_completed_biblionumbers() == {101}


def test_mark_failed_increments_retry_count(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-1")

    repo.mark_failed("run-1", "graph timeout")
    repo.mark_failed("run-1", "graph timeout again")

    row = _rows(repo)[0]
    assert row["status"] == "failed"
    assert row["retry_count"] == 2
    assert row["failed_reason"] == "graph timeout again"

    retry_records = repo.get_retry_eligible(max_retries=3)
    assert len(retry_records) == 1
    assert retry_records[0].biblionumber == 101

    assert repo.get_retry_eligible(max_retries=2) == []


def test_get_recoverable_runs_returns_intermediate_statuses(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-xlsx")
    repo.insert_pending([102], "run-drive")
    repo.insert_pending([103], "run-email")
    repo.insert_pending([104], "run-completed")
    repo.insert_pending([105], "run-failed")

    repo.mark_xlsx_generated("run-xlsx", "xlsx.xlsx")
    repo.mark_xlsx_generated("run-drive", "drive.xlsx")
    repo.mark_gdrive_uploaded("run-drive", "/mnt/drive/drive.xlsx", "/mnt/drive")
    repo.mark_xlsx_generated("run-email", "email.xlsx")
    repo.mark_gdrive_uploaded("run-email", "/mnt/drive/email.xlsx", "/mnt/drive")
    repo.mark_email_sent("run-email", "message-1")
    repo.mark_xlsx_generated("run-completed", "done.xlsx")
    repo.mark_gdrive_uploaded("run-completed", "/mnt/drive/done.xlsx", "/mnt/drive")
    repo.mark_email_sent("run-completed", "message-2")
    repo.mark_completed("run-completed")
    repo.mark_failed("run-failed", "failed before xlsx")

    recoverable = repo.get_recoverable_runs()

    assert {(record.biblionumber, record.status) for record in recoverable} == {
        (101, "xlsx_generated"),
        (102, "gdrive_uploaded"),
        (103, "email_sent"),
    }
