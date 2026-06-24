"""SQLite-схема staged-idempotency для Koha export module."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS exported_records (
    biblionumber          INTEGER NOT NULL,
    run_id                TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'pending'
                          CHECK(status IN (
                              'pending',
                              'xlsx_generated',
                              'gdrive_uploaded',
                              'email_sent',
                              'completed',
                              'failed'
                          )),
    exported_at           TIMESTAMP,
    last_attempt_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    failed_reason         TEXT,
    xlsx_filename         TEXT,
    gdrive_file_path      TEXT,
    gdrive_folder_path    TEXT,
    email_sent_at         TIMESTAMP,
    email_message_id      TEXT,

    PRIMARY KEY (biblionumber, run_id)
);

CREATE INDEX IF NOT EXISTS idx_status_retry
    ON exported_records(status, retry_count);

CREATE UNIQUE INDEX IF NOT EXISTS idx_biblionumber_completed
    ON exported_records(biblionumber)
    WHERE status = 'completed';
"""


class MigrationManager:
    """Застосовує ідемпотентні SQLite-міграції для export state DB."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def migrate(self) -> None:
        db_file = Path(self.db_path)
        if db_file.parent != Path("."):
            db_file.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(SCHEMA_V1)
