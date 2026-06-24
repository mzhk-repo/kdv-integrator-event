"""Atomic copy XLSX у rclone-mounted шлях Google Drive."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class DriveMountCopyResult:
    file_path: str
    folder_path: str
    file_name: str
    was_skipped: bool = False


class ExportDriveMountService:
    def __init__(self, export_root_path: str) -> None:
        if not export_root_path.strip():
            raise ValueError("export_root_path is required")
        self.export_root_path = Path(export_root_path)

    def copy_to_mount(self, xlsx_path: str, run_id: str) -> DriveMountCopyResult:
        if not run_id.strip():
            raise ValueError("run_id is required")

        source_path = Path(xlsx_path)
        year_folder = self.export_root_path / str(datetime.now().year)
        os.makedirs(year_folder, exist_ok=True)

        existing_path = _find_existing_run_file(year_folder, run_id)
        if existing_path is not None:
            return _result(existing_path, was_skipped=True)

        final_path = year_folder / source_path.name
        if final_path.exists():
            return _result(final_path, was_skipped=True)

        part_path = final_path.with_name(f"{final_path.name}.part")
        try:
            with source_path.open("rb") as source_file:
                with part_path.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                    target_file.flush()
                    os.fsync(target_file.fileno())
            os.replace(part_path, final_path)
        except Exception:
            try:
                part_path.unlink()
            except FileNotFoundError:
                pass
            raise

        return _result(final_path, was_skipped=False)


def _find_existing_run_file(year_folder: Path, run_id: str) -> Path | None:
    run_prefix = run_id[:8]
    if not run_prefix:
        return None
    matches = sorted(year_folder.glob(f"*{run_prefix}*.xlsx"))
    return matches[0] if matches else None


def _result(file_path: Path, was_skipped: bool) -> DriveMountCopyResult:
    return DriveMountCopyResult(
        file_path=str(file_path),
        folder_path=str(file_path.parent),
        file_name=file_path.name,
        was_skipped=was_skipped,
    )
