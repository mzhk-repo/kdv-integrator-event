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


def test_cover_service_initialization():
    # just ensure it can be constructed without Koha
    cs = CoverService()
    assert hasattr(cs, "process_book")
