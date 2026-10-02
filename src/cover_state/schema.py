"""Integrator state schema using the export module's SQLite migration runner."""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from src.export_module.db.schema import MigrationManager


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS records (
    record_uid             TEXT NOT NULL PRIMARY KEY,
    cover_source_id        TEXT,
    cover_source_sha256    TEXT,
    cover_asset_sha256     TEXT,
    file_source_id         TEXT,
    file_source_sha256     TEXT,
    dspace_item_uuid       TEXT,
    dspace_bitstream_uuid  TEXT,
    status                 TEXT NOT NULL DEFAULT 'pending'
                           CHECK(status IN ('ok', 'pending', 'failed')),
    retry_count            INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    updated_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);

CREATE TABLE IF NOT EXISTS pending_cover_work (
    record_uid TEXT NOT NULL PRIMARY KEY,
    inputs_sha256 TEXT NOT NULL,
    sources TEXT NOT NULL,
    file_work INTEGER NOT NULL CHECK(file_work IN (0, 1)),
    result TEXT,
    cover_asset_sha256 TEXT NOT NULL
);
"""


def migrate(db_path: str) -> None:
    if not db_path or not Path(db_path).is_absolute():
        raise ValueError("COVER_STATE_DB_PATH must be an absolute file path")
    MigrationManager(db_path, SCHEMA_V1, wal=True, target_version=2).migrate()
    # Add retry scheduling metadata without rebuilding records or touching checkpoints.
    columns = {
        "biblionumber": "INTEGER",
        "retry_reason": "TEXT",
        "defer_reason": "TEXT",
        "next_retry_at": "TIMESTAMP",
        "retry_claimed_at": "TIMESTAMP",
    }
    with closing(sqlite3.connect(db_path)) as connection, connection:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(records)")}
        for name, sql_type in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE records ADD COLUMN {name} {sql_type}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_retry_due "
            "ON records(status, next_retry_at, retry_count)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate the cover state SQLite DB")
    parser.add_argument("--db-path", default=os.environ.get("COVER_STATE_DB_PATH"))
    args = parser.parse_args()
    if not args.db_path:
        parser.error("set COVER_STATE_DB_PATH or pass --db-path")
    migrate(args.db_path)
