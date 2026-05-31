import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import src.export_module.config as export_config  # noqa: E402
from src.export_module.config import (  # noqa: E402
    ConfigValidationError,
    ExportConfig,
    parse_runtime_options,
)


EXPORT_ENV_KEYS = {
    "EXPORT_MODULE_ENABLED",
    "EXPORT_DRY_RUN",
    "EXPORT_GDRIVE_ROOT_PATH",
    "EXPORT_DB_PATH",
    "EXPORT_MARC_MAPPING_PATH",
    "EXPORT_DICTIONARIES_PATH",
    "KOHA_API_URL",
    "KOHA_API_USER",
    "KOHA_API_PASS",
    "KOHA_PAGE_SIZE",
    "GRAPH_TENANT_ID",
    "GRAPH_CLIENT_ID",
    "GRAPH_CLIENT_SECRET",
    "GRAPH_SENDER_USER_ID",
    "GRAPH_TO",
    "MAX_RETRIES",
    "MAX_ATTACHMENT_BYTES",
    "PUSHGATEWAY_URL",
    "ORCHESTRATOR_ENV_FILE",
    "SERVER_ENV",
    "EXPORT_BIBLIONUMBER_FROM",
    "EXPORT_BIBLIONUMBER_TO",
    "EXPORT_MODE",
}


@pytest.fixture(autouse=True)
def clean_export_env(monkeypatch):
    for key in EXPORT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(export_config, "_ENV_BOOTSTRAPPED", False)


def _set_required_env(monkeypatch, tmp_path):
    mapping = tmp_path / "marc_mapping.yaml"
    dictionaries = tmp_path / "export_dictionaries.yaml"
    mapping.write_text("version: 1\n", encoding="utf-8")
    dictionaries.write_text("authorized_values: {}\n", encoding="utf-8")

    values = {
        "EXPORT_MODULE_ENABLED": "true",
        "EXPORT_GDRIVE_ROOT_PATH": "/mnt/drive/KohaExports",
        "EXPORT_DB_PATH": str(tmp_path / "state" / "export_state.db"),
        "EXPORT_MARC_MAPPING_PATH": str(mapping),
        "EXPORT_DICTIONARIES_PATH": str(dictionaries),
        "KOHA_API_URL": "https://koha.example.org/",
        "KOHA_API_USER": "koha-user",
        "KOHA_API_PASS": "koha-pass",
        "GRAPH_TENANT_ID": "00000000-0000-0000-0000-000000000000",
        "GRAPH_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "GRAPH_CLIENT_SECRET": "super-secret",
        "GRAPH_SENDER_USER_ID": "reports@example.org",
        "GRAPH_TO": "target@example.org",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_export_dry_run_env_is_ignored(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXPORT_DRY_RUN", "true")

    config = ExportConfig.from_env()
    runtime_options = parse_runtime_options([])

    assert config.enabled is True
    assert config.koha_base_url == "https://koha.example.org"
    assert runtime_options.dry_run is False
    assert not hasattr(config, "dry_run")


def test_missing_graph_client_secret_raises_safe_error(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.delenv("GRAPH_CLIENT_SECRET")
    monkeypatch.setenv("KOHA_API_PASS", "do-not-leak")

    config = ExportConfig.from_env()

    with pytest.raises(ConfigValidationError) as exc:
        config.validate()

    message = str(exc.value)
    assert "GRAPH_CLIENT_SECRET" in message
    assert "do-not-leak" not in message


def test_export_gdrive_root_path_outside_mount_is_rejected(monkeypatch, tmp_path):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXPORT_GDRIVE_ROOT_PATH", str(tmp_path / "KohaExports"))

    config = ExportConfig.from_env()

    with pytest.raises(ConfigValidationError, match="/mnt/drive"):
        config.validate()


def test_dry_run_cli_sets_runtime_options():
    assert parse_runtime_options(["--dry-run"]).dry_run is True
    assert parse_runtime_options([]).dry_run is False


def test_export_mode_cli_sets_runtime_options():
    assert parse_runtime_options([]).export_mode == "all"
    assert parse_runtime_options(["--export-mode", "file-links"]).export_mode == "file-links"
    assert parse_runtime_options(["--export-mode", "all"]).export_mode == "all"


def test_export_mode_env_is_ignored(monkeypatch):
    monkeypatch.setenv("EXPORT_MODE", "file-links")

    options = parse_runtime_options([])

    assert options.export_mode == "all"


def test_export_mode_rejects_invalid_value():
    with pytest.raises(SystemExit):
        parse_runtime_options(["--export-mode", "bad-mode"])


def test_env_file_uses_sops_style_resolution_without_overriding_existing_env(
    monkeypatch, tmp_path
):
    env_file = tmp_path / "export.env"
    env_file.write_text(
        "\n".join(
            [
                "EXPORT_MODULE_ENABLED=true",
                "KOHA_API_URL=https://from-file.example.org",
                "KOHA_API_USER=file-user",
                "KOHA_API_PASS=file-pass",
                "GRAPH_CLIENT_SECRET=file-secret",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_ENV_FILE", str(env_file))
    monkeypatch.setenv("KOHA_API_URL", "https://already-set.example.org")

    config = ExportConfig.from_env()

    assert config.enabled is True
    assert config.koha_base_url == "https://already-set.example.org"
    assert os.environ["KOHA_API_USER"] == "file-user"


def test_server_env_can_resolve_sops_age_encrypted_env(monkeypatch, tmp_path):
    encrypted_env = tmp_path / "env.dev.enc"
    decrypted_env = tmp_path / "env.decrypted"
    encrypted_env.write_text("encrypted payload", encoding="utf-8")
    decrypted_env.write_text(
        "\n".join(
            [
                "EXPORT_MODULE_ENABLED=true",
                "KOHA_API_URL=https://from-sops.example.org",
                "KOHA_API_USER=sops-user",
                "KOHA_API_PASS=sops-pass",
                "GRAPH_CLIENT_SECRET=sops-secret",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVER_ENV", "dev")
    monkeypatch.setattr(export_config, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        export_config, "_decrypt_sops_env", lambda path: str(decrypted_env)
    )

    config = ExportConfig.from_env()

    assert config.enabled is True
    assert config.koha_base_url == "https://from-sops.example.org"
    assert os.environ["KOHA_API_USER"] == "sops-user"


def test_biblionumber_range_cli_sets_runtime_options():
    options = parse_runtime_options(
        ["--dry-run", "--biblionumber-from", "1000", "--biblionumber-to", "1250"]
    )

    assert options.dry_run is True
    assert options.biblionumber_from == 1000
    assert options.biblionumber_to == 1250


def test_biblionumber_range_env_is_ignored(monkeypatch):
    monkeypatch.setenv("EXPORT_BIBLIONUMBER_FROM", "1000")
    monkeypatch.setenv("EXPORT_BIBLIONUMBER_TO", "1250")

    options = parse_runtime_options([])

    assert options.biblionumber_from is None
    assert options.biblionumber_to is None


def test_biblionumber_range_rejects_invalid_values():
    with pytest.raises(SystemExit):
        parse_runtime_options(["--biblionumber-from", "0"])

    with pytest.raises(SystemExit):
        parse_runtime_options(["--biblionumber-to", "-1"])

    with pytest.raises(SystemExit):
        parse_runtime_options(
            ["--biblionumber-from", "1250", "--biblionumber-to", "1000"]
        )
