"""Генерація XLSX-файлів Koha export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import gettempdir

from openpyxl import Workbook

from src.export_module.marc.mapping_loader import MARCMapping


class XLSXGenerator:
    def __init__(
        self, mapping: MARCMapping, output_dir: str | Path | None = None
    ) -> None:
        self.mapping = mapping
        self.output_dir = (
            Path(output_dir) if output_dir is not None else Path(gettempdir())
        )

    def generate(
        self,
        records: list[dict[str, str | None]],
        run_id: str,
        generated_at: datetime | None = None,
    ) -> str:
        timestamp = generated_at or datetime.now()
        filename = _build_filename(timestamp, run_id)
        output_path = self.output_dir / filename
        self.output_dir.mkdir(parents=True, exist_ok=True)

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Koha Export"

        headers = self.mapping.column_names
        worksheet.append(headers)
        for record in records:
            worksheet.append([record.get(column_name) for column_name in headers])

        workbook.save(output_path)
        return str(output_path)


def _build_filename(generated_at: datetime, run_id: str) -> str:
    timestamp = generated_at.strftime("%Y-%m-%d_%H%M%S")
    return f"export_Koha_{timestamp}_{run_id[:8]}.xlsx"
