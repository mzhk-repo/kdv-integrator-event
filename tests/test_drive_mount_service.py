import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.services.drive_mount_service import (  # noqa: E402
    ExportDriveMountService,
)


def _source_xlsx(tmp_path, name="export_Koha_2026-05-28_120000_run12345.xlsx"):
    path = tmp_path / name
    path.write_bytes(b"xlsx payload")
    return path


def _year_folder(root):
    return root / str(datetime.now().year)


def test_year_folder_is_created_idempotently(tmp_path):
    export_root = tmp_path / "KohaExports"
    source_path = _source_xlsx(tmp_path)
    service = ExportDriveMountService(str(export_root))

    first = service.copy_to_mount(str(source_path), "run12345-0000")
    second = service.copy_to_mount(str(source_path), "run12345-0000")

    assert _year_folder(export_root).is_dir()
    assert Path(first.file_path).exists()
    assert second.was_skipped is True
    assert second.file_path == first.file_path


def test_repeated_run_with_same_run_id_does_not_copy_again(tmp_path):
    export_root = tmp_path / "KohaExports"
    source_path = _source_xlsx(tmp_path)
    service = ExportDriveMountService(str(export_root))

    first = service.copy_to_mount(str(source_path), "run12345-0000")
    source_path.write_bytes(b"changed payload")
    second = service.copy_to_mount(str(source_path), "run12345-0000")

    assert second.was_skipped is True
    assert Path(first.file_path).read_bytes() == b"xlsx payload"


def test_copy_uses_part_file_then_atomic_replace(tmp_path, monkeypatch):
    export_root = tmp_path / "KohaExports"
    source_path = _source_xlsx(tmp_path)
    service = ExportDriveMountService(str(export_root))
    original_replace = os.replace
    calls = []

    def recording_replace(src, dst):
        calls.append((Path(src), Path(dst)))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    result = service.copy_to_mount(str(source_path), "run12345-0000")

    assert calls
    part_path, final_path = calls[0]
    assert part_path.name.endswith(".xlsx.part")
    assert final_path == Path(result.file_path)
    assert not part_path.exists()
    assert final_path.read_bytes() == b"xlsx payload"


def test_copy_failure_cleans_part_file(tmp_path, monkeypatch):
    export_root = tmp_path / "KohaExports"
    source_path = _source_xlsx(tmp_path)
    service = ExportDriveMountService(str(export_root))

    def failing_copy(source, target):
        target.write(b"partial")
        raise OSError("copy failed")

    monkeypatch.setattr("shutil.copyfileobj", failing_copy)

    with pytest.raises(OSError, match="copy failed"):
        service.copy_to_mount(str(source_path), "run12345-0000")

    year_folder = _year_folder(export_root)
    assert not list(year_folder.glob("*.part"))
    assert not list(year_folder.glob("*.xlsx"))


def test_existing_file_with_same_run_id_prefix_is_reused(tmp_path):
    export_root = tmp_path / "KohaExports"
    year_folder = _year_folder(export_root)
    year_folder.mkdir(parents=True)
    existing_path = year_folder / "export_Koha_2026-05-28_110000_run12345.xlsx"
    existing_path.write_bytes(b"already copied")
    source_path = _source_xlsx(
        tmp_path,
        name="export_Koha_2026-05-28_120000_run12345.xlsx",
    )
    service = ExportDriveMountService(str(export_root))

    result = service.copy_to_mount(str(source_path), "run12345-0000")

    assert result.was_skipped is True
    assert result.file_path == str(existing_path)
    assert existing_path.read_bytes() == b"already copied"


def test_service_does_not_use_google_api_upload_client():
    source = Path("src/export_module/services/drive_mount_service.py").read_text(
        encoding="utf-8"
    )

    assert "googleapiclient" not in source
    assert "google-auth" not in source
    assert "drive.file" not in source
