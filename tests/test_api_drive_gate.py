import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

for key, value in {
    "KDV_API_TOKEN": "test-token", "KOHA_API_URL": "http://koha.test",
    "KOHA_OPAC_URL": "http://koha.test", "KOHA_API_USER": "user", "KOHA_API_PASS": "pass",
    "DSPACE_API_URL": "http://dspace.test", "DSPACE_UI_URL": "http://dspace.test",
    "DSPACE_API_USER": "user", "DSPACE_API_PASS": "pass",
}.items():
    os.environ.setdefault(key, value)

from src.app import app  # noqa: E402
from src.core import process_integration_logic  # noqa: E402
from src.cover_state.state_machine import StateMachine  # noqa: E402
from src.services.sources import GoogleDriveSource, SourceResolver  # noqa: E402

UID = "019d4312-1234-7abc-8123-0123456789ab"
CONTENT = b"binary source bytes"
SHA = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    path = str(tmp_path / "state.db")
    monkeypatch.setenv("COVER_STATE_DB_PATH", path)
    monkeypatch.setenv("MAX_RETRY_COUNT", "3")
    state = StateMachine()
    koha = Mock()
    meta = {"record_uid": UID, "file_path": "https://drive.google.com/file/d/primary/view",
            "collection_uuid": "collection"}
    koha.get_biblio_metadata.return_value = meta
    koha.set_success.return_value = True
    koha.set_cover_url.return_value = True
    koha.get_cover_image_url.return_value = "http://koha.test/cover.jpg"
    drive = Mock()
    drive.get_metadata.return_value = {"name": "book.pdf", "mimeType": "application/pdf",
                                       "sha256Checksum": SHA, "size": str(len(CONTENT))}
    drive.download_to_file.side_effect = lambda **kw: Path(kw["destination_path"]).write_bytes(CONTENT)
    resolver = SourceResolver(str(tmp_path), GoogleDriveSource(
        enabled=True, tmp_dir=str(tmp_path / "drive"), drive_client=drive,
    ))
    monkeypatch.setattr("src.core._source_resolver", lambda: resolver)
    cover = Mock(return_value={"status": "success"})
    dspace = Mock(return_value={"handle": "http://dspace.test/handle/1/2", "uuid": "item",
                               "bitstream_uuid": "bitstream"})
    monkeypatch.setattr("src.core.CoverService.process_book", cover)
    monkeypatch.setattr("src.core.run_dspace_workflow", dspace)
    monkeypatch.setattr("src.app._make_clients", lambda: (koha, Mock()))
    return state, koha, meta, drive, cover, dspace


def run(workflow):
    return process_integration_logic("task", 42, koha_client=workflow[1], skip_optimization=True)


def test_authenticated_api_reaches_gate_and_next_cycle_is_zero_work(workflow, monkeypatch):
    state, koha, meta, drive, cover, dspace = workflow
    results = []
    def start(func, *args, **kwargs):
        results.append(func("task", *args, **kwargs))
        return "task"
    monkeypatch.setattr("src.app.task_manager.start_task", start)
    client = app.test_client()
    assert client.post("/kdv/api/integrate/42", headers={"X-KDV-TOKEN": "test-token"}).status_code == 202
    row = state.get(UID)
    assert (row["status"], row["retry_count"], row["file_source_id"], row["file_source_sha256"]) == (
        "ok", 0, "primary", SHA,
    )
    assert (row["dspace_item_uuid"], row["dspace_bitstream_uuid"]) == ("item", "bitstream")
    assert drive.get_metadata.call_count == 1  # Download reuses gate metadata.
    assert drive.download_to_file.call_count == 1
    assert client.post("/kdv/api/integrate/42", headers={"X-KDV-TOKEN": "test-token"}).status_code == 202
    assert results[-1]["status"] == "noop"
    assert drive.get_metadata.call_count == drive.download_to_file.call_count == 1
    assert cover.call_count == dspace.call_count == koha.set_success.call_count == 1
    meta["file_path"] = "https://drive.google.com/file/d/new-id/view"
    assert run(workflow)["status"] == "noop"
    assert state.get(UID)["file_source_id"] == "new-id"
    assert drive.get_metadata.call_count == 2 and drive.download_to_file.call_count == 1


def test_writeback_failure_does_not_commit_source_or_double_increment(workflow):
    state, koha, _, _, _, _ = workflow
    koha.set_success.return_value = False
    with pytest.raises(RuntimeError, match="write-back"):
        run(workflow)
    row = state.get(UID)
    assert (row["status"], row["retry_count"], row["file_source_id"]) == ("pending", 1, None)
    assert run(workflow)["status"] == "deferred"
    assert state.get(UID)["retry_count"] == 1


def test_missing_checksum_stops_before_download_and_downstream(workflow):
    state, _, _, drive, cover, dspace = workflow
    drive.get_metadata.return_value = {"mimeType": "application/vnd.google-apps.document"}
    with pytest.raises(RuntimeError, match="missing sha256Checksum"):
        run(workflow)
    row = state.get(UID)
    assert (row["status"], row["retry_count"]) == ("failed", 3)
    assert run(workflow)["status"] == "deferred"
    drive.download_to_file.assert_not_called()
    cover.assert_not_called()
    dspace.assert_not_called()


def test_concurrent_cycles_do_not_repeat_downstream_work(workflow):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(workflow), range(2)))
    assert sorted(result.get("status", "processed") for result in results) == ["noop", "processed"]
    assert workflow[3].get_metadata.call_count == workflow[5].call_count == 1


def test_changed_cover_skips_unchanged_pdf_and_dspace(workflow):
    state, koha, meta, drive, cover, dspace = workflow
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, file_source_id, file_source_sha256, status) "
            "VALUES (?, 'primary', ?, 'ok')", (UID, SHA),
        )
    meta["cover_path"] = "https://drive.google.com/file/d/cover/view"
    drive.get_metadata.return_value = {"mimeType": "image/png", "name": "cover.png",
                                       "sha256Checksum": SHA, "size": str(len(CONTENT))}
    assert run(workflow)["status"] == "cover_updated"
    drive.get_metadata.assert_called_once_with(file_id="cover", resource_key=None)
    dspace.assert_not_called()
    koha.set_success.assert_not_called()
    koha.set_cover_url.assert_called_once()
    assert state.get(UID)["cover_source_id"] == "cover"
    assert state.get(UID)["status"] == "ok"


def test_existing_dspace_link_cannot_confirm_changed_pdf(workflow):
    state, koha, _, _, _, dspace = workflow
    dspace.return_value["status"] = "linked_existing"
    with pytest.raises(RuntimeError, match="bitstream replacement"):
        run(workflow)
    koha.set_success.assert_not_called()
    assert state.get(UID)["file_source_id"] is None
    assert state.get(UID)["status"] == "pending"


def test_corrupt_download_and_missing_uid_fail_closed(workflow):
    state, _, meta, drive, _, dspace = workflow
    meta["record_uid"] = "not-a-uuid"
    with pytest.raises(ValueError, match="UUIDv7"):
        run(workflow)
    drive.get_metadata.assert_not_called()
    meta["record_uid"] = UID
    drive.download_to_file.side_effect = lambda **kw: Path(kw["destination_path"]).write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="does not match"):
        run(workflow)
    dspace.assert_not_called()
    assert state.get(UID)["retry_count"] == 1


def test_retry_with_two_sources_checks_backoff_once_for_the_cycle(workflow):
    state, _, meta, drive, _, _ = workflow
    state.mark_pending(UID)
    state.record_result(UID, success=False)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute("UPDATE records SET updated_at='2000-01-01 00:00:00'")
    meta['cover_path'] = 'https://drive.google.com/file/d/cover/view'
    drive.get_metadata.side_effect = lambda file_id, resource_key: {
        'sha256Checksum': SHA, 'name': 'book.pdf' if file_id == 'primary' else 'cover.png',
        'mimeType': 'application/pdf' if file_id == 'primary' else 'image/png',
        'size': str(len(CONTENT)),
    }
    assert run(workflow)['uuid'] == 'item'
    assert state.get(UID)['status'] == 'ok'
    assert state.get(UID)['retry_count'] == 0
    assert drive.get_metadata.call_count == 2
