import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import src.export_module.__main__ as export_cli  # noqa: E402
from src.export_module.db.repository import ExportRepository  # noqa: E402


class DummyConfig:
    def __init__(self, db_path=None, validate_error=None):
        self.db_path = str(db_path) if db_path else ":memory:"
        self.validate_error = validate_error
        self.validate_calls = 0

    def validate(self):
        self.validate_calls += 1
        if self.validate_error:
            raise self.validate_error


def test_health_check_validates_config_and_returns_ok(monkeypatch, capsys):
    config = DummyConfig()
    monkeypatch.setattr(export_cli.ExportConfig, "from_env", lambda: config)

    exit_code = export_cli.main(["--health-check"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert config.validate_calls == 1
    assert captured.out.strip() == "health_check_ok"


def test_reset_pending_returns_updated_count(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "export_state.db"
    repo = ExportRepository(str(db_path))
    repo.insert_pending([101, 102], "run-1")
    repo.insert_pending([103], "run-2")
    config = DummyConfig(db_path=db_path)
    monkeypatch.setattr(export_cli.ExportConfig, "from_env", lambda: config)

    exit_code = export_cli.main(["--reset-pending", "run-1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "reset_pending_updated=2"

    with sqlite3.connect(db_path) as connection:
        statuses = dict(
            connection.execute(
                """
                SELECT biblionumber, status
                FROM exported_records
                ORDER BY biblionumber ASC
                """
            ).fetchall()
        )

    assert statuses == {101: "failed", 102: "failed", 103: "pending"}


def test_cli_passes_runtime_options_to_orchestrator(monkeypatch):
    config = DummyConfig()
    captured = {}
    monkeypatch.setattr(export_cli.ExportConfig, "from_env", lambda: config)

    def fake_run_export(received_config, options):
        captured["config"] = received_config
        captured["options"] = options
        return 0

    monkeypatch.setattr(export_cli, "_run_export", fake_run_export)

    exit_code = export_cli.main(
        ["--dry-run", "--export-mode", "file-links", "--biblionumber-from", "1000", "--biblionumber-to", "1250"]
    )

    assert exit_code == 0
    assert captured["config"] is config
    assert captured["options"].dry_run is True
    assert captured["options"].biblionumber_from == 1000
    assert captured["options"].biblionumber_to == 1250
    assert captured["options"].export_mode == "file-links"
