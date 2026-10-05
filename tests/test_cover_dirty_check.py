import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cover_state.state_machine import StateMachine  # noqa: E402
from src.services.sources import GoogleDriveUrlParser  # noqa: E402


def test_unchanged_batch_does_no_downstream_work_or_state_writes(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 5)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.executemany(
            "INSERT INTO records (record_uid, cover_source_id, file_source_id, status) "
            "VALUES (?, ?, ?, 'ok')",
            [(f"record-{i}", f"cover-{i}", f"pdf-{i}") for i in range(100)],
        )
    before = [state.get(f"record-{i}") for i in range(100)]
    drive_metadata = Mock()
    downstream = Mock()
    # The future pipeline dispatches work only after this gate, before pending.
    for i in range(100):
        for source, file_id in (("cover", f"cover-{i}"), ("file", f"pdf-{i}")):
            decision = state.check_source(f"record-{i}", file_id, source=source)
            assert decision == "noop"
            if decision == "needs_sha_check":
                drive_metadata(file_id)
            if decision in ("needs_sha_check", "resume"):
                downstream(file_id)
    assert drive_metadata.call_count == 0
    assert downstream.call_count == 0
    assert [state.get(f"record-{i}") for i in range(100)] == before


@pytest.mark.parametrize("source", ["cover", "file"])
@pytest.mark.parametrize("status", ["ok", "pending", "failed"])
def test_changed_new_and_unfinished_sources(tmp_path, source, status):
    state = StateMachine(str(tmp_path / "state.db"), 5)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, cover_source_id, file_source_id, status, retry_count) "
            "VALUES ('record', 'cover-old', 'pdf-old', ?, 2)", (status,),
        )
    old_id = "cover-old" if source == "cover" else "pdf-old"
    before = state.get("record")
    assert state.check_source("record", "new-id", source=source) == "needs_sha_check"
    assert state.check_source("record", old_id, source=source) == (
        "noop" if status == "ok" else "resume"
    )
    assert state.check_source("new-record", "new-id", source=source) == "needs_sha_check"
    assert state.get("new-record") is None
    assert state.get("record") == before


def test_empty_source_and_exhausted_retry(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 1)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, cover_source_id, status, retry_count) "
            "VALUES ('record', 'old', 'failed', 1)"
        )
    before = state.get("record")
    for value in (None, ""):
        assert state.check_source("record", value, source="cover") == "no_source"
        assert state.check_source("missing", value, source="file") == "no_source"
    assert state.get("record") == before
    assert state.check_source("record", "old", source="cover") == "resume"
    assert state.get_retry_eligible() == []
    assert not state.mark_pending("record")


def test_existing_url_parser_feeds_the_gate(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 5)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, file_source_id, status) "
            "VALUES ('record', 'same-id', 'ok')"
        )
    parser = GoogleDriveUrlParser()
    for url in (
        "https://drive.google.com/file/d/same-id/view",
        "https://drive.google.com/open?id=same-id",
        "https://drive.google.com/uc?id=same-id&resourcekey=key",
    ):
        ref = parser.parse(url, "956$u")
        assert state.check_source("record", ref.file_id, source="file") == "noop"


def test_invalid_arguments_and_missing_stored_id(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 5)
    state.mark_pending("record")
    state.record_result("record", success=True)
    assert state.check_source("record", "new-id", source="cover") == "needs_sha_check"
    with pytest.raises(ValueError, match="source must"):
        state.check_source("record", "new-id", source="typo")
    for value in (" ", 42):
        with pytest.raises(ValueError, match="incoming_file_id"):
            state.check_source("record", value, source="cover")
