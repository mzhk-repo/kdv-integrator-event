import logging
import os

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "kdv-optimizer"))

for key, value in {
    "KDV_API_TOKEN": "test-token",
    "KOHA_API_URL": "http://koha.test",
    "KOHA_OPAC_URL": "http://opac.test",
    "KOHA_API_USER": "test-user",
    "KOHA_API_PASS": "test-pass",
    "DSPACE_API_URL": "http://dspace.test",
    "DSPACE_UI_URL": "http://dspace-ui.test",
    "DSPACE_API_USER": "test-user",
    "DSPACE_API_PASS": "test-pass",
}.items():
    os.environ.setdefault(key, value)

from kdv_optimizer.services import pdf as optimizer_pdf  # noqa: E402
from src.services.pdf import PDFOptimizerClient  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, post_response=None, get_responses=None, post_exc=None):
        self.post_response = post_response or FakeResponse(202, {"status": "processing"})
        self.get_responses = list(get_responses or [])
        self.post_exc = post_exc

    def post(self, *args, **kwargs):
        if self.post_exc:
            raise self.post_exc
        return self.post_response

    def get(self, *args, **kwargs):
        if self.get_responses:
            return self.get_responses.pop(0)
        return FakeResponse(200, {"status": "processing"})


def _pdf_with_size(tmp_path, name, size_mb):
    pdf = tmp_path / name
    with pdf.open("wb") as fh:
        fh.write(b"%PDF-1.4\n")
        fh.truncate(size_mb * 1024 * 1024)
    return pdf


def test_optimizer_needs_optimization_threshold(tmp_path):
    """Умова A: >50MB AND >500KB/стор.; умова B: >100MB; skip=True -> False."""
    small_pdf = _pdf_with_size(tmp_path, "small.pdf", 49)
    rule_a_pdf = _pdf_with_size(tmp_path, "rule-a.pdf", 51)
    rule_b_pdf = _pdf_with_size(tmp_path, "rule-b.pdf", 101)

    assert optimizer_pdf.needs_optimization(str(rule_b_pdf), skip=True) is False
    assert optimizer_pdf.needs_optimization(str(small_pdf), skip=False) is False

    with patch.object(optimizer_pdf, "_count_pages_with_pdfinfo", return_value=87):
        assert optimizer_pdf.needs_optimization(str(rule_a_pdf), skip=False) is True

    with patch.object(optimizer_pdf, "_count_pages_with_pdfinfo") as count_pages:
        assert optimizer_pdf.needs_optimization(str(rule_b_pdf), skip=False) is True
        count_pages.assert_not_called()


def test_optimizer_pdfinfo_crash_fallback(tmp_path):
    """pdfinfo timeout не поширюється назовні; для 60MB файл оптимізація вмикається."""
    candidate = _pdf_with_size(tmp_path, "candidate.pdf", 60)

    with patch.object(
        optimizer_pdf,
        "_count_pages_with_pdfinfo",
        side_effect=subprocess.TimeoutExpired("pdfinfo", 10),
    ):
        assert optimizer_pdf.needs_optimization(str(candidate), skip=False) is True


def test_optimizer_disk_preflight_fail(tmp_path):
    """Недостатньо місця на диску не запускає job у ProcessPoolExecutor."""
    job_id = "11111111-1111-4111-8111-111111111111"
    input_pdf = tmp_path / "input.pdf"
    output_pdf = tmp_path / "output.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\ncontent")
    pool = Mock()

    with patch.object(
        optimizer_pdf, "build_job_paths", return_value=(str(input_pdf), str(output_pdf))
    ), patch.object(optimizer_pdf, "_check_disk_space", return_value=False), patch.object(
        optimizer_pdf, "_optimizer_pool", pool
    ):
        service = optimizer_pdf.PDFOptimizerService()
        with pytest.raises(RuntimeError, match="not enough disk space"):
            service.submit_job(job_id)

    pool.submit.assert_not_called()


def test_optimizer_larger_output_fallback(tmp_path):
    """Якщо Ghostscript створив більший output, сервіс повертає original path."""
    original = tmp_path / "original.pdf"
    output = tmp_path / "optimized.pdf"
    original.write_bytes(b"original")

    def fake_run_ghostscript(input_path, output_path):
        Path(output_path).write_bytes(Path(input_path).read_bytes() + b"-larger")

    with patch.object(optimizer_pdf, "run_ghostscript", side_effect=fake_run_ghostscript):
        result = optimizer_pdf._optimize_pdf(str(original), str(output))

    assert result["status"] == "error"
    assert result["output_path"] == str(original)
    assert result["stats"]["fallback_reason"] == "larger_output"
    assert result["stats"]["candidate_output_path"] == str(output)


def test_optimizer_success_logs_completion(tmp_path, caplog):
    """Optimizer логує старт і успішне завершення без реального Ghostscript."""
    original = tmp_path / "original.pdf"
    output = tmp_path / "optimized.pdf"
    original.write_bytes(b"original-content")

    def fake_run_ghostscript(_input_path, output_path):
        Path(output_path).write_bytes(b"small")

    with patch.object(
        optimizer_pdf, "run_ghostscript", side_effect=fake_run_ghostscript
    ), caplog.at_level(logging.INFO, logger="KDV-Optimizer"):
        result = optimizer_pdf._optimize_pdf(str(original), str(output))

    assert result["status"] == "done"
    assert result["stats"]["fallback_reason"] is None
    assert "PDF optimization process started" in caplog.text
    assert "PDF optimization process completed" in caplog.text
    assert "reduction_pct" in caplog.text


def test_optimizer_client_unavailable(tmp_path):
    """HTTP недоступність optimizer-а повертає fallback і не поширює виняток."""
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    client = PDFOptimizerClient(
        "http://optimizer",
        timeout=1,
        poll_interval=0,
        session=FakeSession(post_exc=requests.exceptions.ConnectionError()),
    )

    result = client.optimize(str(original), "job-id")

    assert result.success is False
    assert result.path == str(original)
    assert result.fallback_reason == "optimizer_unavailable"

from src.services.files import FileService
from src.services.sources import (
    GoogleDriveDownloadError,
    GoogleDriveSource,
    GoogleDriveUrlParser,
    SourceResolutionError,
    SourceResolver,
)
from src.services.covers import CoverService


def test_files_version_and_move(tmp_path):
    # create a fake pdf file
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    orig = source_dir / "test.pdf"
    orig.write_text("dummy")
    # run service
    fs = FileService()
    new_path = fs.version_and_move(str(orig), 123)
    assert os.path.exists(new_path)
    assert "biblio_123_v01.pdf" in new_path

    # second file should increment version
    orig2 = source_dir / "test2.pdf"
    orig2.write_text("dummy2")
    new_path2 = fs.version_and_move(str(orig2), 123)
    assert os.path.exists(new_path2)
    assert "v02" in new_path2


def test_files_move_to_error(tmp_path):
    # simulate a processed folder and file
    base = tmp_path / "base"
    processed = base / "Processed"
    processed.mkdir(parents=True)
    file_path = processed / "biblio_1_v01.pdf"
    file_path.write_text("data")
    fs = FileService()
    fs.move_to_error(str(file_path))
    error_dir = base / "Error"
    assert error_dir.exists()
    moved = error_dir / "biblio_1_v01.pdf"
    assert moved.exists()



def test_source_resolver_primary_local_path(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    resolver = SourceResolver(str(mount))

    resolved = resolver.resolve_primary("books/book.pdf")

    assert resolved.local_path == str(mount / "books" / "book.pdf")
    assert resolved.source_type == "local"
    assert resolved.original_name == "book.pdf"
    assert resolved.temporary is False
    assert resolved.cleanup_paths == ()
    assert resolved.lifecycle_policy == "local_managed"


def test_source_resolver_additional_local_path_is_unmanaged(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    resolver = SourceResolver(str(mount))

    resolved = resolver.resolve_additional("extra/additional.pdf")

    assert resolved.local_path == str(mount / "extra" / "additional.pdf")
    assert resolved.lifecycle_policy == "local_unmanaged"


def test_source_resolver_rejects_path_escape(tmp_path):
    resolver = SourceResolver(str(tmp_path))

    with pytest.raises(SourceResolutionError, match=r"Invalid relative path in 956\$u"):
        resolver.resolve_primary("../secret.pdf")


def test_gdrive_parser_supports_file_d_view_url():
    parser = GoogleDriveUrlParser()

    parsed = parser.parse(
        "https://drive.google.com/file/d/abc123/view?usp=sharing",
        "956$u",
    )

    assert parsed.file_id == "abc123"
    assert parsed.resource_key is None


def test_gdrive_parser_supports_open_and_uc_urls_with_resourcekey():
    parser = GoogleDriveUrlParser()

    open_url = parser.parse(
        "https://drive.google.com/open?id=file-open&resourcekey=resource-open",
        "956$u",
    )
    uc_url = parser.parse(
        "https://drive.google.com/uc?export=download&id=file-uc",
        "956$q",
    )

    assert open_url.file_id == "file-open"
    assert open_url.resource_key == "resource-open"
    assert uc_url.file_id == "file-uc"
    assert uc_url.resource_key is None


def test_gdrive_parser_rejects_folder_links():
    parser = GoogleDriveUrlParser()

    with pytest.raises(SourceResolutionError, match="folder URL is not supported"):
        parser.parse("https://drive.google.com/drive/folders/folder-id", "956$u")


def test_source_resolver_rejects_non_google_urls(tmp_path):
    resolver = SourceResolver(str(tmp_path))

    with pytest.raises(SourceResolutionError, match="only Google Drive file URLs"):
        resolver.resolve_primary("https://example.com/book.pdf")


def test_source_resolver_primary_gdrive_url_is_remote_ephemeral(tmp_path):
    resolver = SourceResolver(str(tmp_path))

    resolved = resolver.resolve_primary(
        "https://drive.google.com/file/d/primary-id/view?resourcekey=primary-key"
    )

    assert resolved.local_path == ""
    assert resolved.source_type == "gdrive"
    assert resolved.original_name == "primary-id"
    assert resolved.temporary is True
    assert resolved.lifecycle_policy == "remote_ephemeral"
    assert resolved.diagnostics["field_name"] == "956$u"
    assert resolved.diagnostics["file_id"] == "primary-id"
    assert resolved.diagnostics["resource_key"] == "primary-key"


def test_source_resolver_additional_gdrive_url_is_remote_ephemeral(tmp_path):
    resolver = SourceResolver(str(tmp_path))

    resolved = resolver.resolve_additional(
        "https://drive.google.com/open?id=additional-id&resourcekey=additional-key"
    )

    assert resolved.source_type == "gdrive"
    assert resolved.lifecycle_policy == "remote_ephemeral"
    assert resolved.diagnostics["field_name"] == "956$q"
    assert resolved.diagnostics["file_id"] == "additional-id"
    assert resolved.diagnostics["resource_key"] == "additional-key"


class FakeDriveClient:
    def __init__(self, metadata=None, content=b"%PDF-1.4\ncontent", error=None):
        self.metadata = metadata or {
            "name": "Book.pdf",
            "mimeType": "application/pdf",
            "size": str(len(content)),
            "capabilities": {"canDownload": True},
        }
        self.content = content
        self.error = error
        self.metadata_calls = []
        self.download_calls = []

    def get_metadata(self, file_id, resource_key):
        self.metadata_calls.append((file_id, resource_key))
        return self.metadata

    def download_to_file(self, file_id, resource_key, destination_path, timeout):
        self.download_calls.append((file_id, resource_key, destination_path, timeout))
        if self.error:
            Path(destination_path).write_bytes(b"partial")
            raise self.error
        Path(destination_path).write_bytes(self.content)


def _gdrive_resolved_source(tmp_path, url=None, drive_client=None, **source_kwargs):
    source = GoogleDriveSource(
        enabled=True,
        tmp_dir=str(tmp_path),
        drive_client=drive_client or FakeDriveClient(),
        **source_kwargs,
    )
    resolver = SourceResolver(str(tmp_path), gdrive_source=source)
    return resolver, resolver.resolve_primary(
        url or "https://drive.google.com/file/d/file-id/view?resourcekey=res-key"
    )


def test_gdrive_source_downloads_pdf_with_atomic_rename(tmp_path):
    drive_client = FakeDriveClient()
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=drive_client)

    resolved = resolver.materialize(parsed)

    assert resolved.source_type == "gdrive"
    assert resolved.lifecycle_policy == "remote_ephemeral"
    assert resolved.temporary is True
    assert resolved.original_name == "Book.pdf"
    assert Path(resolved.local_path).read_bytes() == b"%PDF-1.4\ncontent"
    assert resolved.local_path.endswith(".pdf")
    assert not list(tmp_path.glob("*.part"))
    assert drive_client.metadata_calls == [("file-id", "res-key")]
    assert drive_client.download_calls[0][0:2] == ("file-id", "res-key")
    assert resolved.diagnostics["mime_type"] == "application/pdf"


def test_gdrive_source_reuses_completed_temp_file_when_metadata_matches(tmp_path):
    drive_client = FakeDriveClient(content=b"first-download")
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=drive_client)

    first = resolver.materialize(parsed)
    second = resolver.materialize(parsed)

    assert first.local_path == second.local_path
    assert Path(second.local_path).read_bytes() == b"first-download"
    assert len(drive_client.metadata_calls) == 2
    assert len(drive_client.download_calls) == 1


def test_gdrive_source_ignores_existing_part_file(tmp_path):
    drive_client = FakeDriveClient(content=b"complete-download")
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=drive_client)
    final_path = resolver.gdrive_source._cached_file_path(
        "file-id",
        "res-key",
        drive_client.metadata,
    )
    Path(f"{final_path}.part").write_bytes(b"stale-part")

    resolved = resolver.materialize(parsed)

    assert Path(resolved.local_path).read_bytes() == b"complete-download"
    assert not Path(f"{final_path}.part").exists()
    assert len(drive_client.download_calls) == 1


def test_gdrive_source_cleanup_stale_files_only_inside_tmp_dir(tmp_path):
    tmp_dir = tmp_path / "gdrive"
    tmp_dir.mkdir()
    old_pdf = tmp_dir / "old.pdf"
    old_part = tmp_dir / "old.part"
    fresh_pdf = tmp_dir / "fresh.pdf"
    ignored_txt = tmp_dir / "old.txt"
    outside_pdf = tmp_path / "outside.pdf"
    for file_path in (old_pdf, old_part, fresh_pdf, ignored_txt, outside_pdf):
        file_path.write_bytes(b"x")
    now = 1_700_000_000
    old_ts = now - 100
    fresh_ts = now - 1
    for file_path in (old_pdf, old_part, ignored_txt, outside_pdf):
        os.utime(file_path, (old_ts, old_ts))
    os.utime(fresh_pdf, (fresh_ts, fresh_ts))

    source = GoogleDriveSource(
        enabled=True,
        tmp_dir=str(tmp_dir),
        tmp_ttl_seconds=10,
        drive_client=FakeDriveClient(),
    )

    deleted = source.cleanup_stale_files(now=now)

    assert sorted(Path(path).name for path in deleted) == ["old.part", "old.pdf"]
    assert not old_pdf.exists()
    assert not old_part.exists()
    assert fresh_pdf.exists()
    assert ignored_txt.exists()
    assert outside_pdf.exists()


def test_gdrive_source_rejects_unsupported_mime_type(tmp_path):
    drive_client = FakeDriveClient(metadata={
        "name": "Doc",
        "mimeType": "application/vnd.google-apps.document",
        "size": "10",
        "capabilities": {"canDownload": True},
    })
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=drive_client)

    with pytest.raises(GoogleDriveDownloadError, match="mime type is not allowed"):
        resolver.materialize(parsed)

    assert drive_client.download_calls == []


def test_gdrive_source_rejects_too_large_and_cannot_download(tmp_path):
    too_large = FakeDriveClient(metadata={
        "name": "Large.pdf",
        "mimeType": "application/pdf",
        "size": "11",
        "capabilities": {"canDownload": True},
    })
    resolver, parsed = _gdrive_resolved_source(
        tmp_path, drive_client=too_large, max_bytes=10
    )

    with pytest.raises(GoogleDriveDownloadError, match="too large"):
        resolver.materialize(parsed)

    cannot_download = FakeDriveClient(metadata={
        "name": "Locked.pdf",
        "mimeType": "application/pdf",
        "size": "10",
        "capabilities": {"canDownload": False},
    })
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=cannot_download)

    with pytest.raises(GoogleDriveDownloadError, match="cannot be downloaded"):
        resolver.materialize(parsed)


def test_gdrive_source_cleans_part_file_on_download_error(tmp_path):
    drive_client = FakeDriveClient(error=RuntimeError("network exploded"))
    resolver, parsed = _gdrive_resolved_source(tmp_path, drive_client=drive_client)

    with pytest.raises(RuntimeError, match="network exploded"):
        resolver.materialize(parsed)

    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.pdf"))


def test_gdrive_source_requires_enabled_flag(tmp_path):
    source = GoogleDriveSource(
        enabled=False,
        tmp_dir=str(tmp_path),
        drive_client=FakeDriveClient(),
    )
    resolver = SourceResolver(str(tmp_path), gdrive_source=source)
    parsed = resolver.resolve_primary("https://drive.google.com/open?id=file-id")

    with pytest.raises(GoogleDriveDownloadError, match="disabled"):
        resolver.materialize(parsed)


class FakeCoverKoha:
    def __init__(self):
        self.uploaded = []
        self.checked = False

    def check_cover_exists(self, biblionumber):
        self.checked = True
        return True

    def upload_cover(self, biblionumber, file_path):
        self.uploaded.append((biblionumber, file_path))
        return True


def test_cover_service_uploads_external_cover_without_pdf_generation(tmp_path):
    cover = tmp_path / "manual.jpg"
    cover.write_bytes(b"jpeg-bytes")
    koha = FakeCoverKoha()
    cs = CoverService(koha_client=koha)

    with patch.object(cs, "_generate_image") as generate_mock:
        res = cs.process_book(
            "42",
            None,
            str(tmp_path),
            cover_source_path=str(cover),
        )

    assert res == {"status": "success", "file": str(cover)}
    assert koha.uploaded == [("42", str(cover))]
    assert koha.checked is False
    generate_mock.assert_not_called()


def test_cover_service_initialization():
    # just ensure it can be constructed without Koha
    cs = CoverService()
    assert hasattr(cs, "process_book")
