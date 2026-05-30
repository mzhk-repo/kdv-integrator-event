"""Конфігурація batch-модуля Koha export."""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


_ENV_BOOTSTRAPPED = False
_ENV_TMP_FILE: str | None = None


class ConfigValidationError(ValueError):
    """Помилка безпечної валідації export config без витоку secret-значень."""


@dataclass
class ExportConfig:
    enabled: bool

    koha_base_url: str
    koha_api_user: str
    koha_api_password: str
    koha_page_size: int = 100

    export_gdrive_root_path: str = "/mnt/drive/KohaExports"

    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_sender_user_id: str = ""
    graph_to: str = ""

    max_retries: int = 3
    max_attachment_bytes: int = 15 * 1024 * 1024

    db_path: str = "/data/kdv_export_state/export_state.db"
    marc_mapping_path: str = "config/marc_mapping.yaml"
    export_dictionaries_path: str = "config/export_dictionaries.yaml"
    pushgateway_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ExportConfig":
        bootstrap_environment()

        return cls(
            enabled=_env_bool("EXPORT_MODULE_ENABLED", default=False),
            koha_base_url=_env("KOHA_API_URL").rstrip("/"),
            koha_api_user=_env("KOHA_API_USER"),
            koha_api_password=_env("KOHA_API_PASS"),
            koha_page_size=_env_int("KOHA_PAGE_SIZE", default=100),
            export_gdrive_root_path=_env(
                "EXPORT_GDRIVE_ROOT_PATH", default="/mnt/drive/KohaExports"
            ),
            graph_tenant_id=_env("GRAPH_TENANT_ID"),
            graph_client_id=_env("GRAPH_CLIENT_ID"),
            graph_client_secret=_env("GRAPH_CLIENT_SECRET"),
            graph_sender_user_id=_env("GRAPH_SENDER_USER_ID"),
            graph_to=_env("GRAPH_TO"),
            max_retries=_env_int("MAX_RETRIES", default=3),
            max_attachment_bytes=_env_int(
                "MAX_ATTACHMENT_BYTES", default=15 * 1024 * 1024
            ),
            db_path=_env(
                "EXPORT_DB_PATH",
                default="/data/kdv_export_state/export_state.db",
            ),
            marc_mapping_path=_env(
                "EXPORT_MARC_MAPPING_PATH", default="config/marc_mapping.yaml"
            ),
            export_dictionaries_path=_env(
                "EXPORT_DICTIONARIES_PATH",
                default="config/export_dictionaries.yaml",
            ),
            pushgateway_url=_env("PUSHGATEWAY_URL") or None,
        )

    def validate(self) -> None:
        required = {
            "KOHA_API_URL": self.koha_base_url,
            "KOHA_API_USER": self.koha_api_user,
            "KOHA_API_PASS": self.koha_api_password,
            "GRAPH_TENANT_ID": self.graph_tenant_id,
            "GRAPH_CLIENT_ID": self.graph_client_id,
            "GRAPH_CLIENT_SECRET": self.graph_client_secret,
            "GRAPH_SENDER_USER_ID": self.graph_sender_user_id,
            "GRAPH_TO": self.graph_to,
        }
        for env_name, value in required.items():
            if not value:
                raise ConfigValidationError(f"Missing required environment: {env_name}")

        export_root = Path(self.export_gdrive_root_path)
        if not _is_relative_to(export_root, Path("/mnt/drive")):
            raise ConfigValidationError(
                "EXPORT_GDRIVE_ROOT_PATH must be inside /mnt/drive"
            )

        mapping_path = Path(self.marc_mapping_path)
        if not mapping_path.is_file():
            raise ConfigValidationError(
                f"Mapping file does not exist: {self.marc_mapping_path}"
            )

        dictionaries_path = Path(self.export_dictionaries_path)
        if not dictionaries_path.is_file():
            raise ConfigValidationError(
                f"Export dictionaries file does not exist: "
                f"{self.export_dictionaries_path}"
            )

        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        export_root.mkdir(parents=True, exist_ok=True)


@dataclass
class RuntimeOptions:
    dry_run: bool = False
    biblionumber_from: int | None = None
    biblionumber_to: int | None = None


def parse_runtime_options(argv: list[str] | None = None) -> RuntimeOptions:
    parser = argparse.ArgumentParser(prog="koha-export")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--biblionumber-from", type=int, default=None)
    parser.add_argument("--biblionumber-to", type=int, default=None)
    args = parser.parse_args(argv)
    _validate_runtime_range(args.biblionumber_from, args.biblionumber_to, parser)
    return RuntimeOptions(
        dry_run=args.dry_run,
        biblionumber_from=args.biblionumber_from,
        biblionumber_to=args.biblionumber_to,
    )


def _validate_runtime_range(
    biblionumber_from: int | None,
    biblionumber_to: int | None,
    parser: argparse.ArgumentParser,
) -> None:
    if biblionumber_from is not None and biblionumber_from <= 0:
        parser.error("--biblionumber-from must be a positive integer")
    if biblionumber_to is not None and biblionumber_to <= 0:
        parser.error("--biblionumber-to must be a positive integer")
    if (
        biblionumber_from is not None
        and biblionumber_to is not None
        and biblionumber_from > biblionumber_to
    ):
        parser.error(
            "--biblionumber-from must be less than or equal to --biblionumber-to"
        )


def bootstrap_environment() -> None:
    global _ENV_BOOTSTRAPPED
    if _ENV_BOOTSTRAPPED:
        return

    env_file = _resolve_env_file()
    if env_file:
        if load_dotenv:
            load_dotenv(env_file, override=False)
        else:
            _load_env_file_fallback(env_file)

    _ENV_BOOTSTRAPPED = True


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_env_name(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"dev", "development"}:
        return "dev"
    if value in {"prod", "production"}:
        return "prod"
    return ""


def _cleanup_env_tmp() -> None:
    global _ENV_TMP_FILE
    if not _ENV_TMP_FILE:
        return
    try:
        os.remove(_ENV_TMP_FILE)
    except FileNotFoundError:
        pass
    _ENV_TMP_FILE = None


def _decrypt_sops_env(enc_path: Path) -> str:
    global _ENV_TMP_FILE

    if not shutil.which("sops"):
        return ""

    age_key_file = os.getenv("SOPS_AGE_KEY_FILE", str(Path.home() / ".config/age/keys.txt"))
    if not Path(age_key_file).is_file():
        return ""

    fd, tmp_path = tempfile.mkstemp(prefix="env.", suffix=".decrypted")
    os.close(fd)
    os.chmod(tmp_path, 0o600)
    try:
        with open(tmp_path, "w", encoding="utf-8") as out_file:
            subprocess.run(
                ["sops", "--decrypt", "--age-key-file", age_key_file, str(enc_path)],
                check=True,
                stdout=out_file,
                stderr=subprocess.PIPE,
                text=True,
            )
    except Exception:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        return ""

    _ENV_TMP_FILE = tmp_path
    atexit.register(_cleanup_env_tmp)
    return tmp_path


def _resolve_env_file() -> str:
    project_root = _project_root()

    orchestrator_env_file = os.getenv("ORCHESTRATOR_ENV_FILE", "").strip()
    if orchestrator_env_file and Path(orchestrator_env_file).is_file():
        return orchestrator_env_file

    normalized_env = _normalize_env_name(os.getenv("SERVER_ENV", ""))
    if normalized_env:
        plain_file = project_root / f"env.{normalized_env}"
        if plain_file.is_file():
            return str(plain_file)

        enc_file = project_root / f"env.{normalized_env}.enc"
        if enc_file.is_file():
            decrypted_path = _decrypt_sops_env(enc_file)
            if decrypted_path:
                return decrypted_path

    local_env = project_root / ".env"
    if local_env.is_file():
        return str(local_env)

    return ""


def _load_env_file_fallback(env_file: str) -> None:
    try:
        with open(env_file, "r", encoding="utf-8") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if value and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        return


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigValidationError(f"Invalid integer environment: {key}") from exc


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False
