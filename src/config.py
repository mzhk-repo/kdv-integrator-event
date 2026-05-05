import os
import logging
import sys
import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


_ENV_BOOTSTRAPPED = False
_ENV_TMP_FILE = None


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _normalize_env_name(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"dev", "development"}:
        return "dev"
    if value in {"prod", "production"}:
        return "prod"
    return ""


def _cleanup_env_tmp():
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


def _load_env_file_fallback(env_file: str):
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
                if value and (
                    (value[0] == value[-1] and value[0] in {"'", '"'})
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        return


def bootstrap_environment():
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


bootstrap_environment()


def get_env(key: str, required: bool = True, default: str = None) -> str:
    val = os.getenv(key, default)
    if required and not val:
        raise ValueError(f"CRITICAL ERROR: Environment variable '{key}' is missing.")
    return val


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --- КОНФІГУРАЦІЯ ---

KDV_API_TOKEN = get_env("KDV_API_TOKEN")
KDV_AUTH_MODE = get_env("KDV_AUTH_MODE", required=False, default="legacy").lower()

KDV_CORS_ALLOWLIST = get_env("KDV_CORS_ALLOWLIST", required=False, default="")

# Cloudflare Access (optional in dual/cf-only mode)
CF_ACCESS_TEAM_DOMAIN = get_env("CF_ACCESS_TEAM_DOMAIN", required=False, default="")
CF_ACCESS_AUD = get_env("CF_ACCESS_AUD", required=False, default="")

KOHA_API_URL = get_env("KOHA_API_URL").rstrip("/")
KOHA_OPAC_URL = get_env("KOHA_OPAC_URL").rstrip("/")
KOHA_USER = get_env("KOHA_API_USER")
KOHA_PASS = get_env("KOHA_API_PASS")

DSPACE_API_URL = get_env("DSPACE_API_URL").rstrip("/")
# Додано URL для фронтенду (UI) DSpace, щоб формувати красиві посилання
DSPACE_UI_URL = get_env("DSPACE_UI_URL").rstrip("/")

DSPACE_USER = get_env("DSPACE_API_USER")
DSPACE_PASS = get_env("DSPACE_API_PASS")

DSPACE_SUBMISSION_SECTION = get_env(
    "DSPACE_SUBMISSION_SECTION", required=False, default="traditionalpageone"
)

INTEGRATOR_MOUNT_PATH = get_env("INTEGRATOR_MOUNT_PATH", default="/mnt/drive")
FOLDER_PROCESSED = get_env("FOLDER_PROCESSED", default="Processed")
FOLDER_ERROR = get_env("FOLDER_ERROR", default="Error")

TIMEOUT = 30
UPLOAD_TIMEOUT = 300
