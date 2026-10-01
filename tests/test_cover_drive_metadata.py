import hashlib
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cover_state.drive import (  # noqa: E402
    DriveMetadataError, MissingChecksumError, check_drive_metadata,
)
from src.cover_state.state_machine import StateMachine  # noqa: E402
from src.services.sources import GoogleDriveSource  # noqa: E402

SHA = hashlib.sha256(b"binary content").hexdigest()


@pytest.fixture
def state(tmp_path):
    state = StateMachine(str(tmp_path / "state.db"), 3)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO records (record_uid, cover_source_id, cover_source_sha256, "
            "file_source_id, file_source_sha256, cover_asset_sha256, dspace_item_uuid, status) "
            "VALUES ('record', 'old', ?, 'old', ?, 'asset', 'item', 'ok')", (SHA, SHA),
        )
    return state


@pytest.mark.parametrize("source", ["cover", "file"])
def test_same_content_updates_only_identity_and_next_run_is_zero_work(state, source):
    drive = Mock(spec=GoogleDriveSource)
    drive.get_metadata.return_value = {"mimeType": "application/pdf", "sha256Checksum": SHA.upper()}
    before = state.get("record")
    result = check_drive_metadata(state, "record", "new", source=source,
                                  resource_key="key", drive_source=drive)
    assert (result.action, result.sha256) == ("same_content", SHA)
    drive.get_metadata.assert_called_once_with("new", "key")
    after = state.get("record")
    assert after[f"{source}_source_id"] == "new"
    for key in before.keys() - {f"{source}_source_id", "updated_at"}:
        assert after[key] == before[key]
    drive.get_metadata.reset_mock()
    assert check_drive_metadata(state, "record", "new", source=source, drive_source=drive).action == "noop"
    drive.get_metadata.assert_not_called()
    drive.materialize.assert_not_called()


def test_forced_same_id_refresh_stages_file_work(state):
    drive = Mock(spec=GoogleDriveSource)
    drive.get_metadata.return_value = {"mimeType": "application/pdf", "sha256Checksum": SHA}

    result = check_drive_metadata(
        state, "record", "old", source="file", drive_source=drive, force_refresh=True
    )

    assert (result.action, result.sha256) == ("resource_changed", SHA)
    drive.get_metadata.assert_called_once_with("old", None)
    assert state.get("record")["status"] == "ok"


@pytest.mark.parametrize("uid", ["record", "new-record"])
def test_changed_content_is_pending_without_committing_unconfirmed_source(state, uid):
    drive = Mock(spec=GoogleDriveSource)
    changed_sha = hashlib.sha256(b"changed binary").hexdigest()
    drive.get_metadata.return_value = {"sha256Checksum": changed_sha}
    result = check_drive_metadata(state, uid, "new", source="cover", drive_source=drive)
    assert (result.action, result.sha256) == ("resource_changed", changed_sha)
    row = state.get(uid)
    assert row["status"] == "pending"
    assert row["retry_count"] == 0
    assert row["cover_source_id"] == ("old" if uid == "record" else None)
    assert row["cover_source_sha256"] == (SHA if uid == "record" else None)
    drive.materialize.assert_not_called()


@pytest.mark.parametrize("metadata", [
    {"mimeType": "application/vnd.google-apps.document"},
    {"mimeType": "application/vnd.google-apps.shortcut", "sha256Checksum": ""},
    {"sha256Checksum": None}, {"sha256Checksum": "invalid"}, {"sha256Checksum": 42},
])
def test_missing_or_invalid_checksum_is_permanent_and_preserves_sources(state, metadata, caplog):
    drive = Mock(spec=GoogleDriveSource)
    drive.get_metadata.return_value = metadata
    with pytest.raises(MissingChecksumError, match="sha256Checksum"):
        check_drive_metadata(state, "record", "new", source="file", drive_source=drive)
    row = state.get("record")
    assert (row["status"], row["retry_count"]) == ("failed", 3)
    assert (row["file_source_id"], row["file_source_sha256"]) == ("old", SHA)
    assert "sha256Checksum" in caplog.text
    assert "record" in caplog.text
    assert state.get_retry_eligible() == []
    assert check_drive_metadata(state, "record", "new", source="file", drive_source=drive).action == "deferred"
    assert drive.get_metadata.call_count == 1
    state.reset_retry_count("record")
    drive.get_metadata.return_value = {"sha256Checksum": SHA}
    assert check_drive_metadata(state, "record", "new", source="file", drive_source=drive).action == "resume"
    assert state.get("record")["status"] == "pending"
    assert state.get("record")["file_source_id"] == "old"


@pytest.mark.parametrize("error", [TimeoutError("REDACTED_TOKEN"), RuntimeError("quota REDACTED_TOKEN")])
def test_network_errors_increment_to_cutoff_without_secret_logs(state, error, caplog):
    drive = Mock(spec=GoogleDriveSource)
    drive.get_metadata.side_effect = error
    for attempt in range(1, 4):
        # Simulate separate scheduled runs after elapsed backoff, without sleep.
        with closing(sqlite3.connect(state.db_path)) as connection, connection:
            connection.execute("UPDATE records SET updated_at='2000-01-01 00:00:00'")
        with pytest.raises(DriveMetadataError, match="request failed"):
            check_drive_metadata(state, "record", "new", source="cover", drive_source=drive)
        row = state.get("record")
        assert (row["status"], row["retry_count"]) == ("failed", attempt)
        assert (row["cover_source_id"], row["cover_source_sha256"]) == ("old", SHA)
    assert "REDACTED_TOKEN" not in caplog.text
    assert check_drive_metadata(state, "record", "new", source="cover", drive_source=drive).action == "deferred"
    assert drive.get_metadata.call_count == 3


def test_fast_path_and_resume_do_not_construct_clients(state, monkeypatch):
    factory = Mock(side_effect=AssertionError("Drive must not be constructed"))
    monkeypatch.setattr("src.cover_state.drive.GoogleDriveSource", factory)
    assert check_drive_metadata(state, "record", "old", source="cover").action == "noop"
    assert check_drive_metadata(state, "record", None, source="file").action == "no_source"
    state.mark_pending("record")
    assert check_drive_metadata(state, "record", "old", source="cover").action == "resume"
    # A just-failed record must not make another request during backoff.
    state.record_result("record", success=False)
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute("UPDATE records SET updated_at='2999-01-01 00:00:00'")
    assert check_drive_metadata(state, "record", "new", source="cover").action == "deferred"
    factory.assert_not_called()


def test_real_sdk_metadata_request_fields_and_resource_key(monkeypatch, tmp_path):
    import httplib2
    from googleapiclient.discovery import build
    from googleapiclient.http import HttpRequest

    requests = []
    def execute(request, **kwargs):
        requests.append(request)
        return {"id": "file-id", "sha256Checksum": SHA}

    monkeypatch.setattr(HttpRequest, "execute", execute)
    client = build("drive", "v3", http=httplib2.Http(), static_discovery=True)
    drive = GoogleDriveSource(enabled=True, drive_client=client)
    assert drive.get_metadata("file-id", "resource-key")["sha256Checksum"] == SHA
    query = parse_qs(urlparse(requests[0].uri).query)
    assert "sha256Checksum" in query["fields"][0]
    assert query["supportsAllDrives"] == ["true"]
    assert "resourceKey" not in query
    assert requests[0].headers["X-Goog-Drive-Resource-Keys"] == "file-id/resource-key"
    # Existing materialize callers still use their original field selection.
    drive._get_metadata(client, "file-id", None)
    assert "sha256Checksum" not in parse_qs(urlparse(requests[1].uri).query)["fields"][0]
    downloader = Mock()
    downloader.return_value.next_chunk.return_value = (None, True)
    monkeypatch.setattr("googleapiclient.http.MediaIoBaseDownload", downloader)
    drive._download_to_file(client, "file-id", "resource-key", str(tmp_path / "file.part"))
    media_request = downloader.call_args.args[1]
    assert "resourceKey" not in parse_qs(urlparse(media_request.uri).query)
    assert media_request.headers["X-Goog-Drive-Resource-Keys"] == "file-id/resource-key"


def test_permanent_result_requires_failure(state):
    with pytest.raises(ValueError, match="successful"):
        state.record_result("record", success=True, permanent=True)
