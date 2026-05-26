"""Repository для SQLite state tracking Koha export module."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from src.export_module.db.schema import MigrationManager


@dataclass
class ExportRecord:
    biblionumber: int
    run_id: str
    status: str
    retry_count: int
    failed_reason: Optional[str] = None
    xlsx_filename: Optional[str] = None
    gdrive_file_path: Optional[str] = None
    gdrive_folder_path: Optional[str] = None
    email_sent_at: Optional[str] = None
    email_message_id: Optional[str] = None


class ExportRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        MigrationManager(db_path).migrate()

    def get_completed_biblionumbers(self) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT biblionumber
                FROM exported_records
                WHERE status = 'completed'
                """
            ).fetchall()

        return {int(row["biblionumber"]) for row in rows}

    def get_retry_eligible(self, max_retries: int) -> list[ExportRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM exported_records
                WHERE status = 'failed'
                  AND retry_count < ?
                ORDER BY last_attempt_at ASC, biblionumber ASC
                """,
                (max_retries,),
            ).fetchall()

        return [self._record_from_row(row) for row in rows]

    def get_recoverable_runs(self) -> list[ExportRecord]:
        """Повертає записи у проміжних станах для runbook/recovery."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM exported_records
                WHERE status IN ('xlsx_generated', 'gdrive_uploaded', 'email_sent')
                ORDER BY last_attempt_at ASC, biblionumber ASC
                """
            ).fetchall()

        return [self._record_from_row(row) for row in rows]

    def insert_pending(self, biblionumbers: list[int], run_id: str) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO exported_records (biblionumber, run_id, status)
                VALUES (?, ?, 'pending')
                """,
                [(biblionumber, run_id) for biblionumber in biblionumbers],
            )

    def mark_xlsx_generated(self, run_id: str, xlsx_filename: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE exported_records
                SET status = 'xlsx_generated',
                    xlsx_filename = ?,
                    failed_reason = NULL,
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status = 'pending'
                """,
                (xlsx_filename, run_id),
            )

    def mark_gdrive_uploaded(self, run_id: str, file_path: str, folder_path: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE exported_records
                SET status = 'gdrive_uploaded',
                    gdrive_file_path = ?,
                    gdrive_folder_path = ?,
                    failed_reason = NULL,
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status = 'xlsx_generated'
                """,
                (file_path, folder_path, run_id),
            )

    def mark_email_sent(self, run_id: str, message_id: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE exported_records
                SET status = 'email_sent',
                    email_sent_at = CURRENT_TIMESTAMP,
                    email_message_id = ?,
                    failed_reason = NULL,
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status = 'gdrive_uploaded'
                """,
                (message_id, run_id),
            )

    def mark_completed(self, run_id: str) -> None:
        """Оновлює тільки записи WHERE run_id=:run_id AND status='email_sent'."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE exported_records
                SET status = 'completed',
                    exported_at = CURRENT_TIMESTAMP,
                    failed_reason = NULL,
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status = 'email_sent'
                """,
                (run_id,),
            )

    def mark_failed(self, run_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE exported_records
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    failed_reason = ?,
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status != 'completed'
                """,
                (reason, run_id),
            )

    def reset_stuck_pending(self, run_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE exported_records
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    failed_reason = 'reset_stuck_pending',
                    last_attempt_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                  AND status = 'pending'
                """,
                (run_id,),
            )

        return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ExportRecord:
        return ExportRecord(
            biblionumber=int(row["biblionumber"]),
            run_id=str(row["run_id"]),
            status=str(row["status"]),
            retry_count=int(row["retry_count"]),
            failed_reason=row["failed_reason"],
            xlsx_filename=row["xlsx_filename"],
            gdrive_file_path=row["gdrive_file_path"],
            gdrive_folder_path=row["gdrive_folder_path"],
            email_sent_at=row["email_sent_at"],
            email_message_id=row["email_message_id"],
        )
