import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.cover_state.schema import migrate  # noqa: E402
from src.export_module.db.schema import MigrationManager, SCHEMA_V1  # noqa: E402


def test_cover_migration_is_durable_and_separate_from_export(tmp_path):
    export_path = tmp_path / "export.db"
    cover_path = tmp_path / "cover" / "state.db"
    with closing(sqlite3.connect(export_path)) as connection, connection:
        connection.executescript(SCHEMA_V1)
        connection.execute(
            "INSERT INTO exported_records (biblionumber, run_id) VALUES (42, 'old-run')"
        )

    migrate(str(cover_path))
    with closing(sqlite3.connect(cover_path)) as connection, connection:
        connection.execute("INSERT INTO records (record_uid) VALUES ('record-1')")
    migrate(str(cover_path))
    MigrationManager(str(export_path)).migrate()

    with closing(sqlite3.connect(cover_path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("records",), ("pending_cover_work",)]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(records)")}
        assert columns == {
            "record_uid", "cover_source_id", "cover_source_sha256",
            "cover_asset_sha256", "file_source_id", "file_source_sha256",
            "dspace_item_uuid", "dspace_bitstream_uuid", "status", "retry_count",
            "updated_at",
        }
        row = connection.execute(
            "SELECT status, retry_count, updated_at FROM records WHERE record_uid='record-1'"
        ).fetchone()
        assert row[:2] == ("pending", 0)
        assert row[2]
        indexes = connection.execute("PRAGMA index_list(records)").fetchall()
        unique_indexes = [index[1] for index in indexes if index[2] == 1]
        assert unique_indexes
        assert connection.execute(
            f'PRAGMA index_info("{unique_indexes[0]}")'
        ).fetchone()[2] == "record_uid"
        assert connection.execute(
            "PRAGMA index_info(idx_records_status)"
        ).fetchone()[2] == "status"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO records (record_uid) VALUES ('record-1')")

    with closing(sqlite3.connect(export_path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute(
            "SELECT biblionumber, run_id FROM exported_records"
        ).fetchall() == [(42, "old-run")]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("exported_records",)]


@pytest.mark.parametrize(
    "record_uid,status,retry_count",
    [(None, "pending", 0), ("record-1", "unknown", 0), ("record-1", "failed", -1)],
)
def test_cover_schema_rejects_invalid_state(tmp_path, record_uid, status, retry_count):
    db_path = tmp_path / "state.db"
    migrate(str(db_path))
    with closing(sqlite3.connect(db_path)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO records (record_uid, status, retry_count) VALUES (?, ?, ?)",
                (record_uid, status, retry_count),
            )


@pytest.mark.parametrize("status", ["ok", "pending", "failed"])
def test_cover_schema_accepts_cycle_statuses(tmp_path, status):
    db_path = tmp_path / "state.db"
    migrate(str(db_path))
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, status) VALUES ('record-1', ?)", (status,)
        )
        assert connection.execute("SELECT status FROM records").fetchone()[0] == status


def test_shared_runner_rolls_back_failed_schema(tmp_path):
    db_path = tmp_path / "state.db"
    schema = "CREATE TABLE partial (id INTEGER); SELECT * FROM missing_table;"
    with pytest.raises(sqlite3.OperationalError):
        MigrationManager(str(db_path), schema, wal=True).migrate()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == []


def test_cover_migration_rejects_newer_schema_without_downgrading(tmp_path):
    db_path = tmp_path / "state.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(RuntimeError, match="newer"):
        migrate(str(db_path))
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == []


@pytest.mark.parametrize("db_path", ["", "state.db", ":memory:"])
def test_cover_migration_requires_absolute_path(db_path):
    with pytest.raises(ValueError, match="absolute"):
        migrate(db_path)


def test_additive_cover_checkpoint_migration_preserves_existing_records(tmp_path):
    path = str(tmp_path / 'legacy.db')
    migrate(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('DROP TABLE pending_cover_work')
        connection.execute("INSERT INTO records (record_uid, cover_source_id, cover_source_sha256, status) "
                           "VALUES ('old', 'source', 'confirmed', 'ok')")
        before = connection.execute('SELECT * FROM records').fetchall()
    migrate(path)
    migrate(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('SELECT * FROM records').fetchall() == before
        assert connection.execute('SELECT * FROM pending_cover_work').fetchall() == []
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
