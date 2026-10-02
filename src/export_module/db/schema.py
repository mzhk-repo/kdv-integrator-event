"""SQLite migrations for export and Integrator state."""

from __future__ import annotations

import sqlite3
from contextlib import closing
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
    """Apply additive, idempotent migrations to the existing SQLite state DB."""

    def __init__(self, db_path: str, schema: str = SCHEMA_V1, *, wal: bool = False,
                 target_version: int = 1) -> None:
        self.db_path = db_path
        self.schema = schema
        self.wal = wal
        self.target_version = target_version

    def migrate(self) -> None:
        db_file = Path(self.db_path)
        if db_file.parent != Path("."):
            db_file.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self.db_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self.target_version:
                raise RuntimeError("SQLite schema is newer than this application")
            if self.wal:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode != "wal":
                    raise RuntimeError("SQLite state DB requires WAL journal mode")
            with connection:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + self.schema
                    + f"PRAGMA user_version={self.target_version};\nCOMMIT;"
                )
