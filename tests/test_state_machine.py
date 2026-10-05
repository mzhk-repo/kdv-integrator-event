import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cover_state.state_machine import StateMachine  # noqa: E402


def test_task_manager_interface():
    from src.tasks import task_manager

    tid = task_manager.start_task(lambda tid, x: x, 1)
    assert isinstance(tid, str)
    info = task_manager.get_status(tid)
    assert info is not None


def test_cutoff_persistence_reset_and_success(tmp_path):
    path = str(tmp_path / "state.db")
    state = StateMachine(path, 3)
    assert state.mark_pending("record-1")
    for attempt in range(1, 4):
        state.record_result("record-1", success=False, partial=True)
        row = state.get("record-1")
        assert row["retry_count"] == attempt
        assert row["status"] == ("pending" if attempt < 3 else "failed")
    state = StateMachine(path, 3)
    assert state.get_retry_eligible() == []
    assert not state.mark_pending("record-1")

    # The documented direct SQL reset must also restore retry eligibility.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE records SET retry_count=0 WHERE record_uid='record-1'")
        connection.execute(
            "UPDATE records SET cover_asset_sha256='asset', dspace_item_uuid='item' "
            "WHERE record_uid='record-1'"
        )
    assert [row["record_uid"] for row in state.get_retry_eligible()] == ["record-1"]
    assert state.mark_pending("record-1")
    state.record_result("record-1", success=False)
    assert state.get("record-1")["status"] == "failed"
    assert state.mark_pending("record-1")
    assert state.get("record-1")["retry_count"] == 1
    state.record_result("record-1", success=True)
    row = StateMachine(path, 3).get("record-1")
    assert (row["status"], row["retry_count"]) == ("ok", 0)
    assert (row["cover_asset_sha256"], row["dspace_item_uuid"]) == ("asset", "item")
    assert state.get_retry_eligible() == []


def test_remove_cover_clears_cover_references_but_preserves_pdf_state(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 3)
    state.mark_pending("record")
    state.complete_cycle(
        "record", {"cover": ("cover-id", "cover-sha"), "file": ("pdf-id", "pdf-sha")},
        {"cover_asset_sha256": "asset-sha", "uuid": "item", "bitstream_uuid": "bitstream"},
    )

    assert state.remove_cover("record") is True
    row = state.get("record")
    assert (row["cover_source_id"], row["cover_source_sha256"], row["cover_asset_sha256"]) == (
        None, None, None
    )
    assert (row["file_source_id"], row["file_source_sha256"]) == ("pdf-id", "pdf-sha")
    assert (row["dspace_item_uuid"], row["dspace_bitstream_uuid"]) == ("item", "bitstream")
    assert row["status"] == "ok"


def test_backoff_and_operator_reset(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 5)
    state.mark_pending("retry")
    for attempt in range(1, 5):
        state.record_result("retry", success=False)
        timestamp = datetime.fromisoformat(state.get("retry")["updated_at"]).replace(
            tzinfo=timezone.utc
        )
        delay = timedelta(seconds=2 ** (attempt - 1))
        assert state.get_retry_eligible(now=timestamp + delay - timedelta(microseconds=1)) == []
        assert [r["record_uid"] for r in state.get_retry_eligible(now=timestamp + delay)] == ["retry"]
    state.reset_retry_count("retry")
    assert state.get("retry")["status"] == "failed"
    assert state.get("retry")["retry_count"] == 0
    assert len(state.get_retry_eligible()) == 1
    with pytest.raises(ValueError, match="timezone"):
        state.get_retry_eligible(now=datetime(2026, 1, 1))


def test_persisted_defer_reason_due_time_and_single_claim(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 3)
    state.mark_pending("scheduled")
    state.set_biblionumber("scheduled", 71)
    state.record_result("scheduled", success=False, reason="dspace_timeout")
    row = state.get("scheduled")
    assert row["defer_reason"] == "backoff"
    assert row["retry_reason"] == "dspace_timeout"
    assert row["next_retry_at"]
    assert state.get_retry_eligible(now=datetime(2000, 1, 1, tzinfo=timezone.utc)) == []

    due = datetime.fromisoformat(row["next_retry_at"]).replace(tzinfo=timezone.utc)
    assert state.claim_due_retries(now=due - timedelta(seconds=1)) == []
    assert state.claim_due_retries(now=due)
    assert state.claim_due_retries(now=due) == []


def test_cutoff_has_operator_recovery_state(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 1)
    state.mark_pending("cutoff")
    state.set_biblionumber("cutoff", 72)
    state.record_result("cutoff", success=False, permanent=True, reason="missing_checksum")
    detail = state.get_deferred("cutoff")
    assert detail["reason"] == "cutoff"
    assert detail["retry_count"] == 1
    assert detail["next_retry_at"] is None
    assert state.claim_due_retries() == []
    state.reset_retry_count("cutoff")
    assert state.get("cutoff")["retry_count"] == 0
    assert state.get_deferred("cutoff")["reason"] == "backoff"


def test_scheduler_dispatches_one_valid_due_record(tmp_path, monkeypatch):
    from src.cover_state.retry_scheduler import RetryScheduler

    state = StateMachine(str(tmp_path / "state.db"), 2)
    state.mark_pending("scheduled")
    state.set_biblionumber("scheduled", 71)
    state.record_result("scheduled", success=False, reason="network_timeout")
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "UPDATE records SET next_retry_at='2000-01-01 00:00:00' WHERE record_uid='scheduled'"
        )

    class Koha:
        def get_biblio_metadata(self, _biblionumber):
            return {"record_uid": "scheduled"}

    class Wrapper:
        def __new__(cls):
            return Koha()

    monkeypatch.setattr("src.clients.koha.KohaClientWrapper", Wrapper)
    queued = []
    monkeypatch.setattr("src.cover_state.retry_scheduler.task_manager.start_task",
                        lambda func, bib: queued.append((func, bib)) or "task-id")
    scheduler = RetryScheduler(state_factory=lambda: state)
    scheduler.run_once()
    scheduler.run_once()
    assert len(queued) == 1
    assert queued[0][1] == 71


def test_legacy_rows_receive_a_due_time_or_cutoff_on_upgrade(tmp_path):
    from src.cover_state.schema import SCHEMA_V1
    from src.export_module.db.schema import MigrationManager

    path = str(tmp_path / "legacy.db")
    MigrationManager(path, SCHEMA_V1, wal=True).migrate()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO records (record_uid,status,retry_count,updated_at) VALUES (?, 'failed', ?, ?)",
            [("due", 2, "2026-01-01 00:00:00"), ("cutoff", 3, "2026-01-01 00:00:00")],
        )
    state = StateMachine(path, 3)
    assert state.get("due")["defer_reason"] == "backoff"
    assert state.get("due")["next_retry_at"] == "2026-01-01 00:00:02"
    assert state.get("cutoff")["defer_reason"] == "cutoff"
    assert state.get("cutoff")["next_retry_at"] is None


def test_atomic_failure_increments(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 20)
    state.mark_pending("record")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: state.record_result("record", success=False), range(12)))
    assert state.get("record")["retry_count"] == 12


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "3"])
def test_invalid_explicit_limit_does_not_create_db(tmp_path, limit):
    path = tmp_path / "state.db"
    with pytest.raises(ValueError, match="positive integer"):
        StateMachine(str(path), limit)
    assert not path.exists()


@pytest.mark.parametrize("value", [None, "", "0", "-1", "abc", "1.5"])
def test_invalid_environment_limit(tmp_path, monkeypatch, value):
    monkeypatch.delenv("MAX_RETRY_COUNT", raising=False)
    if value is not None:
        monkeypatch.setenv("MAX_RETRY_COUNT", value)
    with pytest.raises(ValueError, match="MAX_RETRY_COUNT"):
        StateMachine(str(tmp_path / "state.db"))


def test_environment_configuration_and_missing_records(tmp_path, monkeypatch):
    monkeypatch.setenv("COVER_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("MAX_RETRY_COUNT", "1")
    state = StateMachine()
    assert state.get("unknown") is None
    for operation in (state.reset_retry_count, lambda uid: state.record_result(uid, success=False)):
        with pytest.raises(KeyError):
            operation("unknown")
    with pytest.raises(ValueError, match="non-empty"):
        state.mark_pending(" ")
    state.mark_pending("record")
    with pytest.raises(ValueError, match="booleans"):
        state.record_result("record", success="false")
    state.record_result("record", success=False)
    assert state.get_retry_eligible() == []
