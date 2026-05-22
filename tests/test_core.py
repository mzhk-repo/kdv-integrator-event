import logging
import uuid
from unittest.mock import Mock, call, patch

import pytest

from src.core import parse_marc_details, run_dspace_workflow, process_integration_logic
from src.tasks import task_manager
from src.services.pdf import OptimizeResult
from src.koha import KohaClient, KohaRestError


class DummyKohaResponse:
    def __init__(self, status_code, text, content_type="application/json"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}

    def json(self):
        import json

        return json.loads(self.text)


class DummySession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


# stub implementations
class StubKoha:
    def __init__(self):
        self.metadata = {"file_path": "missing.pdf", "collection_uuid": "coll123"}
        self.status_log = []
        self.uploaded_covers = []

    def get_biblio_metadata(self, num):
        return self.metadata

    def _get_biblio_xml(self, num):
        return '<record><datafield tag="856" ind1="4" ind2="0"><subfield code="u">http://example.com/handle/1/2</subfield></datafield></record>'

    def set_status(self, num, status, msg=None):
        self.status_log.append((num, status, msg))

    def set_success(
        self, num, handle_url, item_uuid=None, cover_url=None, primary_download_url=None
    ):
        self.status_log.append((num, "imported", handle_url, primary_download_url))

    def get_cover_image_url(self, num):
        return "http://koha/cover.jpg"

    def upload_cover(self, num, file_path):
        self.uploaded_covers.append((num, file_path))
        return True

    def check_cover_exists(self, num):
        return False


class StubDSpace:
    def __init__(self):
        self.uploaded = []

    def find_item_by_biblionumber(self, num):
        return None

    def create_item_direct(self, uuid, md):
        return {"uuid": "u1", "handle": "1/2"}

    def upload_to_item(self, item_uuid, path, upload_name=None):
        self.uploaded.append((item_uuid, path, upload_name))
        return {"uuid": f"bitstream-{len(self.uploaded)}"}

    def update_metadata(self, item_uuid, md):
        return True

    def get_primary_bitstream(self, item_uuid):
        return {"uuid": "existing-primary-bitstream"}

    def find_item_uuid_by_handle(self, handle):
        return None


def test_parse_marc_rules_basic():
    xml = '<record><datafield tag="245" ind1=" " ind2=" "><subfield code="a">Hello</subfield></datafield></record>'
    out = parse_marc_details(xml)
    assert out.get("dc.title") == "Hello"


def test_koha_metadata_extracts_external_cover_path():
    client = KohaClient.__new__(KohaClient)
    client._get_biblio_xml = lambda _biblio_id: (
        '<record><datafield tag="956" ind1=" " ind2=" ">'
        '<subfield code="u">books/book.pdf</subfield>'
        '<subfield code="p">covers/book.jpg</subfield>'
        '<subfield code="q">extra/a.pdf|extra/b.pdf</subfield>'
        '<subfield code="x">collection-uuid</subfield>'
        '</datafield></record>'
    )

    meta = client.get_biblio_metadata(42)

    assert meta["file_path"] == "books/book.pdf"
    assert meta["cover_path"] == "covers/book.jpg"
    assert meta["additional_files"] == "extra/a.pdf|extra/b.pdf"
    assert meta["collection_uuid"] == "collection-uuid"


def test_koha_rest_auth_error_is_diagnostic():
    client = KohaClient.__new__(KohaClient)
    client.base_url = "http://koha.local"
    client.session = DummySession(
        DummyKohaResponse(401, '{"error":"Basic authentication disabled"}')
    )

    try:
        client.get_biblio_metadata(1)
    except KohaRestError as exc:
        msg = str(exc)
    else:
        raise AssertionError("KohaRestError was not raised")

    assert "HTTP 401" in msg
    assert "Basic authentication disabled" in msg
    assert "No 956 field found" not in msg


def test_run_dspace_with_stubs(tmp_path):
    koha = StubKoha()
    dspace = StubDSpace()
    # simulate existing metadata
    res = run_dspace_workflow(
        5,
        str(tmp_path / "file.pdf"),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
    )
    assert "handle" in res and "uuid" in res
    assert dspace.uploaded[0][0] == "u1"


def test_task_manager_integration(tmp_path):
    # ensure task_manager propagates kwargs
    koha = StubKoha()
    dspace = StubDSpace()
    # use a file that doesn't exist to force error path
    task_id = task_manager.start_task(
        process_integration_logic, 9, koha_client=koha, dspace_client=dspace
    )
    # wait until task finishes (with timeout)
    import time

    deadline = time.time() + 2
    info = None
    while time.time() < deadline:
        info = task_manager.get_status(task_id)
        if info and info["status"] in ("success", "error"):
            break
        time.sleep(0.05)
    assert info is not None
    assert info["status"] in ("error", "success")
    assert koha.status_log


class ExistingItemDSpace(StubDSpace):
    def find_item_by_biblionumber(self, num):
        return {"uuid": "existing-u1", "handle": "1/2"}


class FailingUploadDSpace(StubDSpace):
    def upload_to_item(self, item_uuid, path, upload_name=None):
        self.uploaded.append((item_uuid, path, upload_name))
        raise Exception("upload exploded")


class FakeSuccessOptimizer:
    def optimize(self, original_path, job_id):
        import os

        output = os.path.join(os.environ["OUTPUT_DIR"], f"{job_id}.pdf")
        with open(output, "wb") as stream:
            stream.write(b"small")
        return OptimizeResult(
            success=True,
            path=output,
            fallback_reason=None,
            original_mb=1.0,
            optimized_mb=0.5,
            optimization_time_ms=123,
            thread_wait_ms=10,
        )


class FakeExplodingOptimizer:
    def optimize(self, original_path, job_id):
        raise RuntimeError("optimizer exploded")


class FakeTimeoutFallbackOptimizer:
    def optimize(self, original_path, job_id):
        return OptimizeResult(
            success=False,
            path=original_path,
            fallback_reason="timeout",
            original_mb=1.0,
            optimized_mb=None,
            optimization_time_ms=130000,
            thread_wait_ms=0,
        )


def _force_optimization(monkeypatch):
    monkeypatch.setattr("src.core.needs_optimization", lambda _path, skip: True)
    monkeypatch.setattr(
        "src.core.has_optimizer_disk_space", lambda _path, data_dir: True
    )


def _fixed_job_id(value="11111111-1111-4111-8111-111111111111"):
    return uuid.UUID(value)


def _prepare_optimizer_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "kdv_optimize"
    input_dir = data_dir / "input"
    output_dir = data_dir / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("INPUT_DIR", str(input_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    return input_dir, output_dir


def test_run_dspace_returns_primary_download_url(tmp_path):
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "biblio_73_v01.pdf"
    pdf.write_bytes(b"primary")

    res = run_dspace_workflow(
        73,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        skip_optimization=True,
    )

    assert res["primary_download_url"].endswith(
        "/bitstreams/bitstream-1/download"
    )


def test_run_dspace_uploads_additional_files_without_rename_or_optimizer(
    tmp_path, monkeypatch
):
    koha = StubKoha()
    dspace = StubDSpace()
    primary = tmp_path / "biblio_73_v01.pdf"
    extra_a = tmp_path / "extra-a.pdf"
    extra_b = tmp_path / "nested" / "extra-b.pdf"
    primary.write_bytes(b"primary")
    extra_a.write_bytes(b"extra-a")
    extra_b.parent.mkdir()
    extra_b.write_bytes(b"extra-b")
    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(tmp_path))

    res = run_dspace_workflow(
        73,
        str(primary),
        {
            "collection_uuid": "coll",
            "additional_files": "extra-a.pdf|nested/extra-b.pdf",
        },
        koha_client=koha,
        dspace_client=dspace,
        skip_optimization=True,
    )

    assert res["additional_files_uploaded"] == [
        "extra-a.pdf",
        "nested/extra-b.pdf",
    ]
    assert res["additional_files_failed"] == []
    assert dspace.uploaded == [
        ("u1", str(primary), "biblio_73_v01.pdf"),
        ("u1", str(extra_a), None),
        ("u1", str(extra_b), None),
    ]


def test_run_dspace_additional_missing_and_invalid_are_non_fatal(tmp_path, monkeypatch):
    koha = StubKoha()
    dspace = StubDSpace()
    primary = tmp_path / "biblio_74_v01.pdf"
    primary.write_bytes(b"primary")
    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(tmp_path))

    res = run_dspace_workflow(
        74,
        str(primary),
        {
            "collection_uuid": "coll",
            "additional_files": "missing.pdf|../secret.pdf",
        },
        koha_client=koha,
        dspace_client=dspace,
        skip_optimization=True,
    )

    assert res["handle"].endswith("/handle/1/2")
    assert res["additional_files_uploaded"] == []
    assert res["additional_files_failed"] == [
        {"path": "missing.pdf", "reason": "missing"},
        {"path": "../secret.pdf", "reason": "invalid_path"},
    ]
    assert dspace.uploaded == [("u1", str(primary), "biblio_74_v01.pdf")]


def test_run_dspace_existing_item_uploads_additional_files(tmp_path, monkeypatch):
    koha = StubKoha()
    dspace = ExistingItemDSpace()
    primary = tmp_path / "biblio_75_v01.pdf"
    extra = tmp_path / "extra.pdf"
    primary.write_bytes(b"primary")
    extra.write_bytes(b"extra")
    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(tmp_path))

    res = run_dspace_workflow(
        75,
        str(primary),
        {"collection_uuid": "coll", "additional_files": "extra.pdf"},
        koha_client=koha,
        dspace_client=dspace,
        skip_optimization=True,
    )

    assert res["status"] == "linked_existing"
    assert res["primary_download_url"].endswith(
        "/bitstreams/existing-primary-bitstream/download"
    )
    assert res["additional_files_uploaded"] == ["extra.pdf"]
    assert dspace.uploaded == [("existing-u1", str(extra), None)]


def test_run_dspace_skip_optimization_marks_telemetry(tmp_path):
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original")

    res = run_dspace_workflow(
        5,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        skip_optimization=True,
    )

    assert res["pdf_optimized"] == "skipped_by_user"
    assert res["pdf_fallback_reason"] is None
    assert res["pdf_original_mb"] == 0.0
    assert res["pdf_final_mb"] == 0.0
    assert dspace.uploaded[0][1] == str(pdf)


def test_run_dspace_success_result_contains_pdf_telemetry(tmp_path, monkeypatch):
    input_dir, output_dir = _prepare_optimizer_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr("src.core.needs_optimization", lambda _path, skip: True)
    monkeypatch.setattr(
        "src.core.has_optimizer_disk_space", lambda _path, data_dir: True
    )
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    res = run_dspace_workflow(
        5,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        optimizer_client=FakeSuccessOptimizer(),
    )

    assert res["pdf_optimized"] == "true"
    assert res["pdf_fallback_reason"] is None
    assert res["pdf_original_mb"] == 1.0
    assert res["pdf_final_mb"] == 0.0
    assert res["pdf_pages"] is None
    assert res["pdf_optimization_time_ms"] == 123
    assert res["pdf_thread_wait_ms"] == 10
    assert "pdf_disk_free_mb" in res
    assert dspace.uploaded[0][1] != str(pdf)
    assert list(input_dir.iterdir()) == []
    assert list(output_dir.iterdir()) == []


def test_run_dspace_optimized_upload_keeps_rename_first_filename(tmp_path, monkeypatch):
    _prepare_optimizer_dirs(tmp_path, monkeypatch)
    _force_optimization(monkeypatch)
    koha = StubKoha()
    dspace = StubDSpace()
    processed_dir = tmp_path / "Processed"
    processed_dir.mkdir()
    pdf = processed_dir / "biblio_73_v01.pdf"
    pdf.write_bytes(b"original-content")

    res = run_dspace_workflow(
        73,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        optimizer_client=FakeSuccessOptimizer(),
    )

    assert res["pdf_optimized"] == "true"
    assert dspace.uploaded[0][1] != str(pdf)
    assert dspace.uploaded[0][1].endswith(".pdf")
    assert dspace.uploaded[0][2] == "biblio_73_v01.pdf"


def test_run_dspace_logs_optimizer_handoff(tmp_path, monkeypatch, caplog):
    _prepare_optimizer_dirs(tmp_path, monkeypatch)
    _force_optimization(monkeypatch)
    job_uuid = _fixed_job_id()
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    with patch("src.core.uuid.uuid4", return_value=job_uuid), caplog.at_level(
        logging.INFO, logger="KDV-Core"
    ):
        res = run_dspace_workflow(
            5,
            str(pdf),
            {"collection_uuid": "coll"},
            koha_client=koha,
            dspace_client=dspace,
            optimizer_client=FakeSuccessOptimizer(),
        )

    assert res["pdf_optimized"] == "true"
    assert "PDF optimization job submitted to optimizer" in caplog.text
    assert str(job_uuid) in caplog.text
    assert "PDF optimization completed" in caplog.text


def test_run_dspace_upload_failure_cleans_optimizer_tmp_files(tmp_path, monkeypatch):
    input_dir, output_dir = _prepare_optimizer_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr("src.core.needs_optimization", lambda _path, skip: True)
    monkeypatch.setattr(
        "src.core.has_optimizer_disk_space", lambda _path, data_dir: True
    )
    koha = StubKoha()
    dspace = FailingUploadDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    try:
        run_dspace_workflow(
            5,
            str(pdf),
            {"collection_uuid": "coll"},
            koha_client=koha,
            dspace_client=dspace,
            optimizer_client=FakeSuccessOptimizer(),
        )
    except Exception as exc:
        assert "upload exploded" in str(exc)
    else:
        raise AssertionError("upload failure was not raised")

    assert list(input_dir.iterdir()) == []
    assert list(output_dir.iterdir()) == []


def test_run_dspace_optimizer_exception_falls_back_to_original(tmp_path, monkeypatch):
    input_dir, output_dir = _prepare_optimizer_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr("src.core.needs_optimization", lambda _path, skip: True)
    monkeypatch.setattr(
        "src.core.has_optimizer_disk_space", lambda _path, data_dir: True
    )
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    res = run_dspace_workflow(
        5,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        optimizer_client=FakeExplodingOptimizer(),
    )

    assert res["pdf_optimized"] == "false"
    assert res["pdf_fallback_reason"] == "exception"
    assert dspace.uploaded[0][1] == str(pdf)
    assert list(input_dir.iterdir()) == []
    assert list(output_dir.iterdir()) == []


def test_core_cleanup_on_exception(tmp_path, monkeypatch):
    """finally видаляє tmp файли навіть якщо optimizer.optimize() кидає виняток."""
    input_dir, output_dir = _prepare_optimizer_dirs(tmp_path, monkeypatch)
    _force_optimization(monkeypatch)
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    res = run_dspace_workflow(
        5,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        optimizer_client=FakeExplodingOptimizer(),
    )

    assert res["pdf_optimized"] == "false"
    assert res["pdf_fallback_reason"] == "exception"
    assert dspace.uploaded[0][1] == str(pdf)
    assert list(input_dir.iterdir()) == []
    assert list(output_dir.iterdir()) == []


def test_core_cleanup_on_dspace_exception(tmp_path, monkeypatch):
    """finally видаляє tmp файли навіть якщо dspace upload_to_item() кидає виняток."""
    input_dir, output_dir = _prepare_optimizer_dirs(tmp_path, monkeypatch)
    _force_optimization(monkeypatch)
    job_uuid = _fixed_job_id()
    input_tmp = str(input_dir / f"{job_uuid}.pdf")
    output_tmp = str(output_dir / f"{job_uuid}.pdf")
    koha = StubKoha()
    dspace = FailingUploadDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    with patch("src.core.uuid.uuid4", return_value=job_uuid), patch(
        "src.core.os.remove"
    ) as remove_mock:
        with pytest.raises(Exception, match="upload exploded"):
            run_dspace_workflow(
                5,
                str(pdf),
                {"collection_uuid": "coll"},
                koha_client=koha,
                dspace_client=dspace,
                optimizer_client=FakeSuccessOptimizer(),
            )

    remove_mock.assert_has_calls([call(input_tmp), call(output_tmp)], any_order=False)
    assert remove_mock.call_count == 2


def test_optimizer_fallback_does_not_fail_archive(tmp_path, monkeypatch):
    """Fallback оптимізації не переводить архівацію в error."""
    _prepare_optimizer_dirs(tmp_path, monkeypatch)
    _force_optimization(monkeypatch)
    koha = StubKoha()
    dspace = StubDSpace()
    pdf = tmp_path / "file.pdf"
    pdf.write_bytes(b"original-content")

    res = run_dspace_workflow(
        5,
        str(pdf),
        {"collection_uuid": "coll"},
        koha_client=koha,
        dspace_client=dspace,
        optimizer_client=FakeTimeoutFallbackOptimizer(),
    )

    assert res["handle"].endswith("/handle/1/2")
    assert res["pdf_optimized"] == "false"
    assert res["pdf_fallback_reason"] == "timeout"
    assert dspace.uploaded[0][1] == str(pdf)


def test_external_cover_upload_runs_when_pdf_missing(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    cover = mount / "covers" / "manual.jpg"
    cover.parent.mkdir()
    cover.write_bytes(b"jpeg-bytes")
    koha = StubKoha()
    koha.metadata = {
        "file_path": "books/missing.pdf",
        "cover_path": "covers/manual.jpg",
        "collection_uuid": "coll",
    }
    dspace = StubDSpace()
    run_dspace = Mock()

    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(mount))
    monkeypatch.setattr("src.core.run_dspace_workflow", run_dspace)

    with pytest.raises(Exception, match="File not found on disk"):
        process_integration_logic(
            "task-id",
            5,
            koha_client=koha,
            dspace_client=dspace,
        )

    assert koha.uploaded_covers == [("5", str(cover))]
    run_dspace.assert_not_called()
    assert any(
        status == "error" and msg == "File missing: books/missing.pdf"
        for _, status, msg in koha.status_log
    )


def test_external_cover_upload_runs_when_pdf_field_empty(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    cover = mount / "manual.jpg"
    cover.write_bytes(b"jpeg-bytes")
    koha = StubKoha()
    koha.metadata = {
        "file_path": None,
        "cover_path": "manual.jpg",
        "collection_uuid": "coll",
    }

    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(mount))

    with pytest.raises(Exception, match="File not found on disk"):
        process_integration_logic(
            "task-id", 5, koha_client=koha, dspace_client=StubDSpace()
        )

    assert koha.uploaded_covers == [("5", str(cover))]


def test_cover_relative_path_cannot_escape_mount(tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    mount.mkdir()
    pdf = mount / "book.pdf"
    pdf.write_bytes(b"pdf")
    koha = StubKoha()
    koha.metadata = {
        "file_path": "book.pdf",
        "cover_path": "../secret.jpg",
        "collection_uuid": "coll",
    }

    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(mount))

    with pytest.raises(ValueError, match=r"Invalid relative path in 956\$p"):
        process_integration_logic(
            "task-id", 5, koha_client=koha, dspace_client=StubDSpace()
        )


def test_hard_limit_does_not_prevent_optimization_path(tmp_path, monkeypatch):
    """Поточний LIMIT_ERROR зупиняє workflow до optimization path; контракт не змінюється."""
    mount = tmp_path / "mount"
    mount.mkdir()
    huge_pdf = mount / "huge.pdf"
    huge_pdf.write_bytes(b"%PDF-1.4\n")
    koha = StubKoha()
    koha.metadata = {"file_path": "huge.pdf", "collection_uuid": "coll"}
    dspace = StubDSpace()
    run_dspace = Mock()

    monkeypatch.setattr("src.core.INTEGRATOR_MOUNT_PATH", str(mount))
    monkeypatch.setattr("src.core.os.path.getsize", lambda _path: 251 * 1024 * 1024)
    monkeypatch.setattr("src.core.run_dspace_workflow", run_dspace)

    with pytest.raises(Exception, match="FILE TOO LARGE"):
        process_integration_logic(
            "task-id",
            5,
            koha_client=koha,
            dspace_client=dspace,
            optimizer_client=FakeTimeoutFallbackOptimizer(),
        )

    run_dspace.assert_not_called()
    assert any(status == "error" and "FILE TOO LARGE" in msg for _, status, msg in koha.status_log)

