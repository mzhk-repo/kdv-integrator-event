import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.db.schema import MigrationManager  # noqa: E402


STAGED_STATUSES = [
    "pending",
    "xlsx_generated",
    "gdrive_uploaded",
    "email_sent",
    "completed",
    "failed",
]


def _connect(db_path):
    return sqlite3.connect(str(db_path))


def test_schema_accepts_staged_statuses(tmp_path):
    db_path = tmp_path / "export_state.db"
    manager = MigrationManager(str(db_path))

    manager.migrate()
    manager.migrate()

    with _connect(db_path) as connection:
        for index, status in enumerate(STAGED_STATUSES, start=1):
            connection.execute(
                """
                INSERT INTO exported_records (biblionumber, run_id, status)
                VALUES (?, ?, ?)
                """,
                (index, f"run-{index}", status),
            )

        rows = connection.execute(
            "SELECT status FROM exported_records ORDER BY biblionumber"
        ).fetchall()

    assert [row[0] for row in rows] == STAGED_STATUSES


def test_schema_rejects_invalid_status(tmp_path):
    db_path = tmp_path / "export_state.db"
    MigrationManager(str(db_path)).migrate()

    with _connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO exported_records (biblionumber, run_id, status)
                VALUES (?, ?, ?)
                """,
                (1, "run-1", "sent_by_smtp"),
            )


def test_schema_allows_only_one_completed_record_per_biblionumber(tmp_path):
    db_path = tmp_path / "export_state.db"
    MigrationManager(str(db_path)).migrate()

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO exported_records (biblionumber, run_id, status)
            VALUES (?, ?, ?)
            """,
            (1, "run-1", "completed"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO exported_records (biblionumber, run_id, status)
                VALUES (?, ?, ?)
                """,
                (1, "run-2", "completed"),
            )

        connection.execute(
            """
            INSERT INTO exported_records (biblionumber, run_id, status)
            VALUES (?, ?, ?)
            """,
            (1, "run-3", "failed"),
        )
