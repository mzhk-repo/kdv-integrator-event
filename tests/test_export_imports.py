import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


EXPORT_MODULES = [
    "src.export_module",
    "src.export_module.__main__",
    "src.export_module.config",
    "src.export_module.db.schema",
    "src.export_module.db.repository",
    "src.export_module.koha.client",
    "src.export_module.koha.filters",
    "src.export_module.marc.mapping_loader",
    "src.export_module.marc.parser",
    "src.export_module.observability.logger",
    "src.export_module.orchestrator",
    "src.export_module.services.drive_mount_service",
    "src.export_module.services.graph_email_service",
    "src.export_module.xlsx.generator",
]


def test_all_imports_succeed():
    for module_name in EXPORT_MODULES:
        importlib.import_module(module_name)
