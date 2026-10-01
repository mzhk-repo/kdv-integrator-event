import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

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
from src.config import DSPACE_UI_URL  # noqa: E402

UID = "019d4312-1234-7abc-8123-0123456789ab"
with BytesIO() as pdf_bytes:
    Image.new('RGB', (900, 1200), 'red').save(pdf_bytes, 'PDF')
    CONTENT = pdf_bytes.getvalue()
SHA = hashlib.sha256(CONTENT).hexdigest()
with BytesIO() as image_bytes:
    Image.new('RGB', (900, 1200), 'blue').save(image_bytes, 'PNG')
    COVER_CONTENT = image_bytes.getvalue()
COVER_SHA = hashlib.sha256(COVER_CONTENT).hexdigest()


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    path = str(tmp_path / "state.db")
    monkeypatch.setenv("COVER_STATE_DB_PATH", path)
    monkeypatch.setenv("MAX_RETRY_COUNT", "3")
    storage = tmp_path / 'covers'
    (storage / 'assets').mkdir(parents=True)
    (storage / '.incoming').mkdir(mode=0o700)
    monkeypatch.setenv('COVERS_STORAGE_PATH', str(storage))
    state = StateMachine()
    koha = Mock()
    meta = {"record_uid": UID, "file_path": "https://drive.google.com/file/d/primary/view",
            "collection_uuid": "collection"}
    koha.get_biblio_metadata.return_value = meta
    koha.set_success.return_value = True
    koha.set_cover_url.return_value = True
    def set_success(*args, **kwargs):
        meta['dspace_uuid'] = kwargs.get('item_uuid')
        meta['dspace_links'] = [link for link in (args[1], kwargs.get('primary_download_url')) if link]
        if kwargs.get('cover_url'):
            meta['cover_asset_sha256'] = kwargs['cover_url']
        return True
    def set_cover(*args):
        meta['cover_asset_sha256'] = args[1]
        return True
    koha.set_success.side_effect = set_success
    koha.set_cover_url.side_effect = set_cover
    koha.get_cover_image_url.return_value = "http://koha.test/cover.jpg"
    drive = Mock()
    drive.get_metadata.return_value = {"name": "book.pdf", "mimeType": "application/pdf",
                                       "sha256Checksum": SHA, "size": str(len(CONTENT))}
    drive.download_to_file.side_effect = lambda **kw: Path(kw["destination_path"]).write_bytes(
        COVER_CONTENT if kw['file_id'] == 'cover' else CONTENT
    )
    resolver = SourceResolver(str(tmp_path), GoogleDriveSource(
        enabled=True, tmp_dir=str(tmp_path / "drive"), drive_client=drive,
    ))
    monkeypatch.setattr("src.core._source_resolver", lambda: resolver)
    cover = Mock(return_value={"status": "success"})
    dspace = Mock(return_value={"handle": "http://dspace.test/handle/1/2", "uuid": "item",
                               "bitstream_uuid": "bitstream",
                               "primary_download_url": "http://dspace.test/bitstreams/bitstream/download"})
    monkeypatch.setattr("src.core.CoverService.process_book", cover)
    monkeypatch.setattr("src.core.run_dspace_workflow", dspace)
    monkeypatch.setattr("src.app._make_clients", lambda: (koha, Mock()))
    return state, koha, meta, drive, cover, dspace


def run(workflow, dspace_client=None):
    return process_integration_logic("task", 42, koha_client=workflow[1],
                                    dspace_client=dspace_client, skip_optimization=True)


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
    assert cover.call_count == 0
    assert dspace.call_count == koha.set_success.call_count == 1
    assert koha.set_success.call_args.kwargs['cover_url'] == row['cover_asset_sha256']
    assert state.get_cover_work(UID) is None
    asset = Path(os.environ['COVERS_STORAGE_PATH']) / 'assets' / f"{row['cover_asset_sha256']}.webp"
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == row['cover_asset_sha256']
    with Image.open(asset) as image:
        assert image.format == 'WEBP' and image.size == (600, 800)
    meta["file_path"] = "https://drive.google.com/file/d/new-id/view"
    assert run(workflow)["status"] == "noop"
    assert state.get(UID)["file_source_id"] == "new-id"
    assert drive.get_metadata.call_count == 2 and drive.download_to_file.call_count == 1


def test_unchanged_source_repairs_missing_dspace_handle_link(workflow):
    state, koha, meta, drive, cover, dspace_workflow = workflow
    assert run(workflow)["uuid"] == "item"
    handle_url = "http://dspace.test/handle/1/2"
    meta["dspace_links"] = ["https://catalog.test/existing"]
    def repair_links(_biblio, file_url, handle_url):
        meta["dspace_links"] = [file_url, handle_url]
        return True
    koha.repair_dspace_links.side_effect = repair_links
    dspace_client = Mock()
    dspace_client.get_item.return_value = {"handle": "1/2"}
    dspace_client.get_primary_bitstream.return_value = {"uuid": "primary-bitstream"}
    drive_calls = (drive.get_metadata.call_count, drive.download_to_file.call_count)

    result = run(workflow, dspace_client=dspace_client)

    assert result == {"status": "links_repaired"}
    dspace_client.get_item.assert_called_once_with("item")
    dspace_client.get_primary_bitstream.assert_called_once_with("item")
    handle_url = f"{DSPACE_UI_URL}/handle/1/2"
    file_url = f"{DSPACE_UI_URL}/bitstreams/primary-bitstream/download"
    koha.repair_dspace_links.assert_called_once_with(42, file_url, handle_url)
    assert meta["dspace_links"] == [
        file_url, handle_url,
    ]
    assert (drive.get_metadata.call_count, drive.download_to_file.call_count) == drive_calls
    assert dspace_workflow.call_count == 1
    cover.assert_not_called()
    assert state.get(UID)["status"] == "ok"


def test_writeback_failure_does_not_commit_source_or_double_increment(workflow):
    state, koha, _, _, _, _ = workflow
    koha.set_success.return_value = False
    koha.set_success.side_effect = None
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
                                       "sha256Checksum": COVER_SHA, "size": str(len(COVER_CONTENT))}
    assert run(workflow)["status"] == "cover_updated"
    drive.get_metadata.assert_called_once_with(file_id="cover", resource_key=None)
    dspace.assert_not_called()
    koha.set_success.assert_not_called()
    koha.set_cover_url.assert_called_once()
    cover.assert_not_called()
    asset_sha = state.get(UID)['cover_asset_sha256']
    assert koha.set_cover_url.call_args.args == (42, asset_sha)
    assert state.get(UID)["cover_source_id"] == "cover"
    assert state.get(UID)["status"] == "ok"


def test_local_cover_with_drive_pdf_keeps_legacy_writer(workflow):
    state, koha, meta, _, legacy_cover, _ = workflow
    meta['cover_path'] = 'covers/local.jpg'
    assert run(workflow)['uuid'] == 'item'
    legacy_cover.assert_called_once()
    assert koha.set_success.call_args.kwargs['cover_url'] == 'http://koha.test/cover.jpg'
    assert state.get(UID)['status'] == 'ok'
    assert state.get_cover_work(UID) is None


def test_existing_dspace_link_cannot_confirm_changed_pdf(workflow):
    state, koha, _, _, _, dspace = workflow
    dspace.return_value["status"] = "linked_existing"
    with pytest.raises(RuntimeError, match="bitstream replacement"):
        run(workflow)
    koha.set_success.assert_not_called()
    assert state.get(UID)["file_source_id"] is None
    assert state.get(UID)["status"] == "pending"


def test_changed_pdf_retry_reuses_uploaded_bitstream_and_deletes_old_after_koha(workflow):
    state, koha, meta, _, _, dspace_workflow = workflow
    assert state.mark_pending(UID)
    old_sha = "a" * 64
    state.complete_cycle(UID, {"file": ("primary", old_sha)}, {
        "uuid": "item", "bitstream_uuid": "old-bitstream",
    })
    meta["file_path"] = "https://drive.google.com/file/d/new-source/view"

    upload_result = {
        "uuid": "item", "bitstream_uuid": "new-bitstream",
        "old_bitstream_uuid": "old-bitstream",
        "handle": "http://dspace.test/handle/1/2",
        "primary_download_url": "http://dspace.test/bitstreams/new-bitstream/download",
        "status": "replaced",
    }

    def replace_pdf(*_args, **kwargs):
        assert kwargs["replace_existing"] is True
        assert _args[2]['previous_dspace_bitstream_uuid'] == 'old-bitstream'
        assert _args[2]['previous_dspace_item_uuid'] == 'item'
        kwargs["result_callback"](upload_result)
        return upload_result

    dspace_workflow.side_effect = replace_pdf
    dspace_client = Mock()
    calls = {"write": 0}

    def set_success(*args, **kwargs):
        calls["write"] += 1
        if calls["write"] == 1:
            return False
        meta["dspace_uuid"] = kwargs["item_uuid"]
        meta["cover_asset_sha256"] = kwargs["cover_url"]
        meta["dspace_links"] = [kwargs["primary_download_url"], args[1]]
        return True

    koha.set_success.side_effect = set_success
    with pytest.raises(RuntimeError, match="write-back"):
        run(workflow, dspace_client=dspace_client)
    assert dspace_client.delete_bitstream.call_count == 0
    checkpoint = state.get_cover_work(UID)
    assert checkpoint["result"]["bitstream_uuid"] == "new-bitstream"
    assert checkpoint["result"]["old_bitstream_uuid"] == "old-bitstream"

    retry_due(state)
    result = run(workflow, dspace_client=dspace_client)

    assert result["bitstream_uuid"] == "new-bitstream"
    assert dspace_workflow.call_count == 1
    dspace_client.delete_bitstream.assert_called_once_with("old-bitstream")
    assert state.get(UID)["dspace_bitstream_uuid"] == "new-bitstream"
    assert state.get(UID)["file_source_sha256"] == SHA
    assert state.get_cover_work(UID) is None


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
        'sha256Checksum': SHA if file_id == 'primary' else COVER_SHA,
        'name': 'book.pdf' if file_id == 'primary' else 'cover.png',
        'mimeType': 'application/pdf' if file_id == 'primary' else 'image/png',
        'size': str(len(CONTENT)),
    }
    assert run(workflow)['uuid'] == 'item'
    assert state.get(UID)['status'] == 'ok'
    assert state.get(UID)['retry_count'] == 0
    assert drive.get_metadata.call_count == 2


def external_cover(workflow, *, cover_only=False):
    _, _, meta, drive, _, _ = workflow
    meta['cover_path'] = 'https://drive.google.com/file/d/cover/view'
    if cover_only:
        meta['file_path'] = None
    drive.get_metadata.side_effect = lambda file_id, resource_key: {
        'sha256Checksum': SHA if file_id == 'primary' else COVER_SHA,
        'name': 'book.pdf' if file_id == 'primary' else 'cover.png',
        'mimeType': 'application/pdf' if file_id == 'primary' else 'image/png',
        'size': str(len(CONTENT if file_id == 'primary' else COVER_CONTENT)),
    }


@pytest.mark.parametrize('resume', [False, True])
def test_pdf_replacement_cleanup_with_unchanged_cover_or_resume(workflow, resume):
    state, koha, meta, drive, cover, dspace_workflow = workflow
    external_cover(workflow)
    run(workflow)
    initial_cover_sha = state.get(UID)['cover_asset_sha256']
    cover_downloads = sum(
        call.kwargs['file_id'] == 'cover' for call in drive.download_to_file.call_args_list
    )
    if resume:
        state.mark_pending(UID)
    else:
        meta['file_path'] = 'https://drive.google.com/file/d/new-pdf/view'
        with closing(sqlite3.connect(state.db_path)) as connection, connection:
            connection.execute('UPDATE records SET file_source_sha256=?', ('a' * 64,))
        drive.get_metadata.side_effect = lambda **kwargs: {
            'sha256Checksum': SHA, 'name': 'new.pdf', 'mimeType': 'application/pdf',
            'size': str(len(CONTENT)),
        }
    result = {
        'uuid': 'item', 'bitstream_uuid': 'new-bitstream',
        'old_bitstream_uuids': ['bitstream'], 'status': 'replaced',
        'handle': 'http://dspace.test/handle/1/2',
        'primary_download_url': 'http://dspace.test/bitstreams/new-bitstream/download',
    }
    def replace_pdf(*args, **kwargs):
        assert kwargs['replace_existing'] is True
        assert args[2]['previous_dspace_bitstream_uuid'] == 'bitstream'
        kwargs['result_callback'](result)
        return result
    dspace_workflow.side_effect = replace_pdf
    client = Mock()
    client.delete_bitstream.side_effect = RuntimeError('delete unavailable')
    with pytest.raises(RuntimeError, match='delete unavailable'):
        run(workflow, dspace_client=client)
    assert state.get_cover_work(UID)['result']['old_bitstream_uuids'] == ['bitstream']
    assert state.get(UID)['status'] == 'pending'
    uploads = dspace_workflow.call_count
    retry_due(state)
    client.delete_bitstream.side_effect = None
    assert run(workflow, dspace_client=client)['bitstream_uuid'] == 'new-bitstream'
    assert dspace_workflow.call_count == uploads
    assert client.delete_bitstream.call_count == 2
    assert state.get(UID)['status'] == 'ok'
    assert state.get(UID)['cover_asset_sha256'] == initial_cover_sha
    assert state.get_cover_work(UID) is None
    cover.assert_not_called()
    if not resume:
        # Unchanged explicit cover reuses its durable asset, including cleanup retry.
        assert sum(
            call.kwargs['file_id'] == 'cover' for call in drive.download_to_file.call_args_list
        ) == cover_downloads


def retry_due(state):
    with closing(sqlite3.connect(state.db_path)) as connection, connection:
        connection.execute("UPDATE records SET updated_at='2000-01-01 00:00:00'")


@pytest.mark.parametrize('cover_only', [True, False])
def test_external_cover_retry_only_repeats_koha_after_reopening_state(workflow, monkeypatch, cover_only):
    import src.core as core
    state, koha, _, drive, legacy_cover, dspace = workflow
    external_cover(workflow, cover_only=cover_only)
    normalize = Mock(wraps=core.download_and_normalize)
    monkeypatch.setattr(core, 'download_and_normalize', normalize)
    writer = koha.set_cover_url if cover_only else koha.set_success
    succeed = writer.side_effect
    writer.side_effect = None
    writer.return_value = False
    with pytest.raises(RuntimeError, match='write-back'):
        run(workflow)
    row = state.get(UID)
    assert (row['status'], row['retry_count'], row['cover_source_id'], row['file_source_id']) == (
        'pending', 1, None, None,
    )
    asset = Path(os.environ['COVERS_STORAGE_PATH']) / 'assets' / f"{row['cover_asset_sha256']}.webp"
    before = asset.stat()
    assert state.get_cover_work(UID) is not None
    calls = (drive.get_metadata.call_count, drive.download_to_file.call_count, normalize.call_count, dspace.call_count)
    retry_due(state)
    writer.side_effect = succeed
    reopened = StateMachine(state.db_path, max_retry_count=3)
    result = process_integration_logic('retry', 42, koha_client=koha, state_machine=reopened,
                                       skip_optimization=True)
    assert result['cover_asset_sha256'] == row['cover_asset_sha256']
    assert (drive.get_metadata.call_count, drive.download_to_file.call_count, normalize.call_count, dspace.call_count) == calls
    assert (asset.stat().st_ino, asset.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert reopened.get(UID)['status'] == 'ok'
    assert reopened.get(UID)['retry_count'] == 0
    assert reopened.get(UID)['cover_source_id'] == 'cover'
    assert reopened.get(UID)['cover_source_sha256'] == COVER_SHA
    assert reopened.get_cover_work(UID) is None
    assert run(workflow)['status'] == 'noop'
    legacy_cover.assert_not_called()


def test_pdf_cover_retry_reuses_published_asset_and_completed_dspace(workflow, monkeypatch):
    import src.core as core
    state, koha, _, drive, legacy_cover, dspace = workflow
    render = Mock(wraps=core.render_pdf_cover)
    monkeypatch.setattr(core, 'render_pdf_cover', render)
    succeed = koha.set_success.side_effect
    koha.set_success.side_effect = None
    koha.set_success.return_value = False
    with pytest.raises(RuntimeError, match='write-back'):
        run(workflow)
    row = state.get(UID)
    assert (row['status'], row['retry_count'], row['file_source_id']) == ('pending', 1, None)
    assert state.get_cover_work(UID)['result']['uuid'] == 'item'
    asset = Path(os.environ['COVERS_STORAGE_PATH']) / 'assets' / f"{row['cover_asset_sha256']}.webp"
    before = asset.stat()
    calls = (drive.get_metadata.call_count, drive.download_to_file.call_count,
             render.call_count, dspace.call_count)
    retry_due(state)
    koha.set_success.side_effect = succeed
    reopened = StateMachine(state.db_path, max_retry_count=3)
    assert process_integration_logic('retry', 42, koha_client=koha, state_machine=reopened,
                                     skip_optimization=True)['cover_asset_sha256'] == row['cover_asset_sha256']
    assert (drive.get_metadata.call_count, drive.download_to_file.call_count,
            render.call_count, dspace.call_count) == calls
    assert (asset.stat().st_ino, asset.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert reopened.get(UID)['status'] == 'ok' and reopened.get_cover_work(UID) is None
    assert run(workflow)['status'] == 'noop'
    legacy_cover.assert_not_called()


def test_bad_pdf_fails_only_its_record(workflow):
    state, koha, meta, drive, legacy_cover, dspace = workflow
    bad = b'not a PDF'
    drive.get_metadata.return_value = {
        'name': 'bad.pdf', 'mimeType': 'application/pdf',
        'sha256Checksum': hashlib.sha256(bad).hexdigest(), 'size': str(len(bad)),
    }
    drive.download_to_file.side_effect = lambda **kw: Path(kw['destination_path']).write_bytes(bad)
    with pytest.raises(ValueError, match='PDF first page'):
        run(workflow)
    assert (state.get(UID)['status'], state.get(UID)['retry_count']) == ('failed', 3)
    assert state.get(UID)['file_source_id'] is None
    koha.set_success.assert_not_called()
    dspace.assert_not_called()
    legacy_cover.assert_not_called()

    next_uid = '019d4312-1234-7abc-8123-0123456789ac'
    meta['record_uid'] = next_uid
    meta['file_path'] = 'https://drive.google.com/file/d/good-pdf/view'
    drive.get_metadata.return_value = {
        'name': 'good.pdf', 'mimeType': 'application/pdf',
        'sha256Checksum': SHA, 'size': str(len(CONTENT)),
    }
    drive.download_to_file.side_effect = lambda **kw: Path(kw['destination_path']).write_bytes(CONTENT)
    assert run(workflow)['cover_asset_sha256'] == state.get(next_uid)['cover_asset_sha256']
    assert state.get(next_uid)['status'] == 'ok'
    assert state.get(UID)['status'] == 'failed'


def test_cover_readback_failure_is_pending_and_retry_does_not_convert(workflow):
    state, koha, _, drive, _, _ = workflow
    external_cover(workflow, cover_only=True)
    succeed = koha.set_cover_url.side_effect
    koha.set_cover_url.side_effect = None
    with pytest.raises(RuntimeError, match=r'957\$c read-back'):
        run(workflow)
    assert state.get(UID)['status'] == 'pending'
    assert state.get(UID)['cover_source_id'] is None
    retry_due(state)
    koha.set_cover_url.side_effect = succeed
    run(workflow)
    assert drive.get_metadata.call_count == drive.download_to_file.call_count == 1


def test_pending_cover_corruption_blocks_koha_write(workflow):
    state, koha, _, _, _, _ = workflow
    external_cover(workflow, cover_only=True)
    koha.set_cover_url.side_effect = None
    koha.set_cover_url.return_value = False
    with pytest.raises(RuntimeError):
        run(workflow)
    digest = state.get(UID)['cover_asset_sha256']
    asset = Path(os.environ['COVERS_STORAGE_PATH']) / 'assets' / f'{digest}.webp'
    asset.write_bytes(b'corrupt')
    retry_due(state)
    koha.set_cover_url.reset_mock()
    with pytest.raises(Exception):
        run(workflow)
    koha.set_cover_url.assert_not_called()
    assert state.get(UID)['status'] == 'pending'


def test_cover_retry_cutoff_and_manual_reset_keep_checkpoint(workflow):
    state, koha, _, drive, _, _ = workflow
    external_cover(workflow, cover_only=True)
    succeed = koha.set_cover_url.side_effect
    koha.set_cover_url.side_effect = None
    koha.set_cover_url.return_value = False
    for _ in range(3):
        retry_due(state)
        with pytest.raises(RuntimeError, match='write-back'):
            run(workflow)
    assert (state.get(UID)['status'], state.get(UID)['retry_count']) == ('failed', 3)
    assert state.get_cover_work(UID) is not None
    assert run(workflow)['status'] == 'deferred'
    state.reset_retry_count(UID)
    koha.set_cover_url.side_effect = succeed
    run(workflow)
    assert state.get(UID)['status'] == 'ok'
    assert drive.get_metadata.call_count == drive.download_to_file.call_count == 1


def test_input_change_invalidates_staged_work(workflow):
    state, koha, meta, drive, _, _ = workflow
    external_cover(workflow, cover_only=True)
    succeed = koha.set_cover_url.side_effect
    koha.set_cover_url.side_effect = None
    koha.set_cover_url.return_value = False
    with pytest.raises(RuntimeError):
        run(workflow)
    retry_due(state)
    meta['cover_path'] = 'https://drive.google.com/file/d/changed-cover/view'
    drive.download_to_file.side_effect = lambda **kw: Path(kw['destination_path']).write_bytes(COVER_CONTENT)
    koha.set_cover_url.side_effect = succeed
    run(workflow)
    assert state.get(UID)['cover_source_id'] == 'changed-cover'
    assert drive.get_metadata.call_count == drive.download_to_file.call_count == 2


def test_legacy_confirmed_drive_cover_is_migrated_to_webp(workflow):
    state, _, _, drive, legacy, _ = workflow
    external_cover(workflow, cover_only=True)
    state.mark_pending(UID)
    state.complete_cycle(UID, {'cover': ('cover', COVER_SHA)}, {})
    assert state.get(UID)['cover_asset_sha256'] is None
    run(workflow)
    assert state.get(UID)['cover_asset_sha256'] is not None
    assert state.get(UID)['status'] == 'ok'
    legacy.assert_not_called()


def test_external_cover_failed_dspace_retains_asset_and_never_writes_koha(workflow):
    state, koha, _, drive, _, dspace = workflow
    external_cover(workflow)
    dspace.side_effect = RuntimeError('DSpace unavailable')
    with pytest.raises(RuntimeError, match='DSpace unavailable'):
        run(workflow)
    koha.set_success.assert_not_called()
    assert state.get(UID)['status'] == 'pending'
    assert state.get_cover_work(UID)['result'] is None
    retry_due(state)
    dspace.side_effect = None
    run(workflow)
    assert state.get(UID)['status'] == 'ok'
    # Cover metadata/download was not repeated; the unfinished PDF may resume.
    assert [call.kwargs['file_id'] for call in drive.download_to_file.call_args_list].count('cover') == 1


@pytest.mark.parametrize('field,expected', [('dspace_uuid', r'957\$3'), ('dspace_links', '856')])
def test_pdf_writeback_readback_checks_all_managed_values(workflow, field, expected):
    state, koha, meta, _, _, _ = workflow
    external_cover(workflow)
    succeed = koha.set_success.side_effect
    def incomplete(*args, **kwargs):
        succeed(*args, **kwargs)
        meta.pop(field, None)
        return True
    koha.set_success.side_effect = incomplete
    with pytest.raises(RuntimeError, match=expected):
        run(workflow)
    assert state.get(UID)['status'] == 'pending'
    assert state.get(UID)['file_source_id'] is None
    assert state.get_cover_work(UID)['result'] is not None


def test_changed_pdf_gate_keeps_confirmed_cover_on_zero_work_path(workflow):
    state, _, meta, drive, _, _ = workflow
    external_cover(workflow)
    run(workflow)
    # The PDF changes; its gate enters pending before the cover is checked.
    meta['file_path'] = 'https://drive.google.com/file/d/changed-pdf/view'
    drive.get_metadata.reset_mock()
    drive.get_metadata.side_effect = lambda file_id, resource_key: {
        'sha256Checksum': hashlib.sha256(b'new pdf').hexdigest(),
        'name': 'book.pdf', 'mimeType': 'application/pdf', 'size': '7',
    }
    drive.download_to_file.side_effect = lambda **kw: Path(kw['destination_path']).write_bytes(b'new pdf')
    run(workflow)
    drive.get_metadata.assert_called_once_with(file_id='changed-pdf', resource_key=None)
    assert state.get(UID)['cover_source_id'] == 'cover'
