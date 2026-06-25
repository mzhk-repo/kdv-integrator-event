import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = PROJECT_ROOT / "scripts" / "entrypoint.sh"


def test_entrypoint_exports_runtime_env_payload(tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    payload = secrets_dir / "app_env_payload"
    payload.write_text(
        "\n".join(
            [
                "EXPORT_MODULE_ENABLED=true",
                "EXPORT_MARC_MAPPING_PATH=config/marc_mapping.yaml",
                "EXPORT_DICTIONARIES_PATH=config/export_dictionaries.yaml",
            ]
        ),
        encoding="utf-8",
    )
    (secrets_dir / "KDV_API_TOKEN").write_text("legacy-token", encoding="utf-8")

    env = {
        "PATH": os.environ.get("PATH", ""),
        "RUNTIME_ENV_PAYLOAD": str(payload),
        "SECRETS_DIR": str(secrets_dir),
    }
    result = subprocess.run(
        [
            str(ENTRYPOINT),
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ.get('EXPORT_MARC_MAPPING_PATH', '')); "
                "print(os.environ.get('EXPORT_DICTIONARIES_PATH', '')); "
                "print(os.environ.get('KDV_API_TOKEN', ''))"
            ),
        ],
        check=True,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )

    assert result.stdout.splitlines() == [
        "config/marc_mapping.yaml",
        "config/export_dictionaries.yaml",
        "legacy-token",
    ]
