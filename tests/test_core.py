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

    def get_biblio_metadata(self, num):
        return self.metadata

    def _get_biblio_xml(self, num):
        return '<record><datafield tag="856" ind1="4" ind2="0"><subfield code="u">http://example.com/handle/1/2</subfield></datafield></record>'

    def set_status(self, num, status, msg=None):
        self.status_log.append((num, status, msg))

    def set_success(self, num, handle_url, item_uuid=None, cover_url=None):
        self.status_log.append((num, "imported", handle_url))

    def get_cover_image_url(self, num):
        return "http://koha/cover.jpg"


class StubDSpace:
    def __init__(self):
        self.uploaded = []

    def find_item_by_biblionumber(self, num):
        return None

    def create_item_direct(self, uuid, md):
        return {"uuid": "u1", "handle": "1/2"}

    def upload_to_item(self, item_uuid, path):
        self.uploaded.append((item_uuid, path))
        return True

    def update_metadata(self, item_uuid, md):
        return True

    def find_item_uuid_by_handle(self, handle):
        return None


def test_parse_marc_rules_basic():
    xml = '<record><datafield tag="245" ind1=" " ind2=" "><subfield code="a">Hello</subfield></datafield></record>'
    out = parse_marc_details(xml)
    assert out.get("dc.title") == "Hello"


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


class FailingUploadDSpace(StubDSpace):
    def upload_to_item(self, item_uuid, path):
        self.uploaded.append((item_uuid, path))
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
