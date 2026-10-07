#!/usr/bin/env python3
"""Create and restore-check SQLite cover state backups."""

from __future__ import annotations

import argparse
import logging
import os
import pwd
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("KDV-CoverStateBackup")
BACKUP_NAME = re.compile(r"^state-(\d{8}T\d{12}Z)-([0-9]+)\.sqlite3$")
ENV_KEYS = {
    "COVER_STATE_HOST_PATH", "COVER_STATE_BACKUP_HOST_PATH",
    "COVER_STATE_LOCAL_RETENTION_DAYS", "COVER_STATE_CLOUD_RETENTION_DAYS",
    "BACKUP_RCLONE_REMOTE", "BACKUP_RCLONE_FOLDER", "BACKUP_RCLONE_CONFIG",
    "RCLONE_CONFIG", "COVERS_STORAGE_HOST_PATH", "COVER_ASSETS_BACKUP_HOST_PATH",
}


def _database(path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    return sqlite3.connect(path, timeout=30)


def _check_database(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise ValueError("SQLite quick_check failed")
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'"
    ).fetchone() is None:
        raise ValueError("Database does not contain the cover state records table")


def verify_backup(backup_path: Path) -> int:
    """Restore a backup into a temporary DB and check the restored contents."""
    resolved_backup = backup_path.resolve(strict=True)
    if not resolved_backup.is_file():
        raise ValueError("Backup must be an existing regular file")
    if backup_path.is_symlink() and resolved_backup.parent != backup_path.parent.resolve():
        raise ValueError("Backup symlink must point within its directory")
    with tempfile.TemporaryDirectory(prefix="kdv-state-restore-") as temp_dir:
        restored = Path(temp_dir) / "state.db"
        with closing(_database(resolved_backup)) as source, closing(sqlite3.connect(restored)) as target:
            source.backup(target)
            _check_database(target)
            records = target.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    logger.info("Restore check passed: %s records from %s", records, resolved_backup)
    return records


def prune_backups(backup_dir: Path, retention_days: int) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    for candidate in backup_dir.iterdir():
        match = BACKUP_NAME.fullmatch(candidate.name)
        if match and not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink()


def copy_cloud_backup(local_path: Path, remote_path: str, retention_days: int) -> None:
    """Copy and read-back verify the snapshot with the rclone CLI."""
    remote = remote_path.split(":", 1)[0]
    candidates = []
    for key in ("BACKUP_RCLONE_CONFIG", "RCLONE_CONFIG"):
        configured = os.environ.get(key, "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user:
        try:
            candidates.append(Path(pwd.getpwnam(sudo_user).pw_dir) / ".config/rclone/rclone.conf")
        except KeyError:
            pass
    candidates.extend((
        Path("/var/lib/docker-plugins/rclone/config/rclone.conf"),
        Path.home() / ".config/rclone/rclone.conf",
    ))
    config = None
    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file() or not os.access(candidate, os.R_OK):
            continue
        seen.add(candidate)
        result = subprocess.run(
            ["rclone", "--config", str(candidate), "listremotes"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and any(line.strip().removesuffix(":") == remote for line in result.stdout.splitlines()):
            config = candidate
            break
    if config is None:
        raise ValueError(f"No readable rclone config contains remote '{remote}'")
    rclone = ["rclone", "--config", str(config)]
    name = local_path.name
    snapshot_remote = f"{remote_path}/{name}"
    subprocess.run([*rclone, "copyto", str(local_path), snapshot_remote], check=True)
    with tempfile.TemporaryDirectory(prefix="kdv-rclone-verify-") as temp_dir:
        downloaded = Path(temp_dir) / name
        subprocess.run([*rclone, "copyto", snapshot_remote, str(downloaded)], check=True)
        verify_backup(downloaded)
    subprocess.run([*rclone, "copyto", str(local_path), f"{remote_path}/latest.sqlite3"], check=True)
    subprocess.run([
        *rclone, "delete", remote_path, "--min-age", f"{retention_days}d",
        "--include", "state-*.sqlite3",
    ], check=True)
    logger.info("Cloud backup complete: %s/%s", remote_path, name)


def create_backup(db_path: Path, backup_dir: Path, retention_days: int) -> Path:
    """Take a consistent SQLite snapshot, publish it atomically, then prune old snapshots."""
    if not db_path.is_absolute() or not db_path.is_file() or db_path.is_symlink():
        raise ValueError("State DB path must be an absolute regular file")
    if not backup_dir.is_absolute() or backup_dir == Path("/"):
        raise ValueError("Backup directory must be an absolute non-root path")
    if backup_dir.is_symlink():
        raise ValueError("Backup directory must not be a symlink")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise ValueError("Backup directory must be a real directory")
    os.chmod(backup_dir, 0o700)
    latest_path = backup_dir / "latest.sqlite3"
    if latest_path.exists() and not latest_path.is_symlink():
        raise ValueError("latest.sqlite3 exists and is not a symlink")
    if latest_path.is_symlink() and latest_path.resolve().parent != backup_dir.resolve():
        raise ValueError("latest.sqlite3 symlink must point within the backup directory")
    if db_path.resolve() == (backup_dir / "latest.sqlite3").resolve():
        raise ValueError("Backup destination must differ from the state DB")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    fd, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=backup_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with closing(_database(db_path, readonly=False)) as source, closing(sqlite3.connect(temp_path)) as target:
            _check_database(source)
            source.backup(target)
            _check_database(target)
            records = target.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        with temp_path.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
        final_path = backup_dir / f"state-{stamp}-{os.getpid()}.sqlite3"
        os.replace(temp_path, final_path)
        os.chmod(final_path, 0o600)

        latest_tmp = backup_dir / f".latest-{os.getpid()}-{stamp}"
        latest_tmp.symlink_to(final_path.name)
        os.replace(latest_tmp, latest_path)
        prune_backups(backup_dir, retention_days)
        directory_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        logger.info("Backup complete: %s records=%s", final_path, records)
        return final_path
    finally:
        temp_path.unlink(missing_ok=True)


def load_server_env_from_host(environment_file: Path = Path("/etc/environment")) -> None:
    """Read only SERVER_ENV from the host file if the process did not set it."""
    if os.environ.get("SERVER_ENV", "").strip() or os.environ.get("ORCHESTRATOR_ENV_FILE"):
        return
    if not environment_file.is_file():
        return
    for line in environment_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key != "SERVER_ENV":
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            os.environ["SERVER_ENV"] = value
        return


def load_cover_state_environment() -> None:
    """Load selected backup settings from the host's selected dotenv file."""
    load_server_env_from_host()
    selected = os.environ.get("ORCHESTRATOR_ENV_FILE", "").strip()
    if selected:
        env_path = Path(selected)
        if not env_path.is_file():
            raise ValueError("ORCHESTRATOR_ENV_FILE does not exist")
        raw = env_path.read_text(encoding="utf-8")
    else:
        environment = os.environ.get("SERVER_ENV", "").strip().lower()
        normalized = {
            "dev": "dev", "development": "dev", "prod": "prod", "production": "prod",
        }.get(environment)
        if not normalized:
            raise ValueError("SERVER_ENV must identify dev or prod")
        env_path = Path(__file__).resolve().parents[1] / f"env.{normalized}.enc"
        if not env_path.is_file() or not shutil.which("sops"):
            raise ValueError(f"encrypted environment file or sops unavailable for {normalized}")
        sops_env = os.environ.copy()
        if sops_env.get("SOPS_AGE_KEY"):
            sops_env.pop("SOPS_AGE_KEY_FILE", None)
        else:
            age_key = sops_env.get("SOPS_AGE_KEY_FILE")
            if age_key and (not Path(age_key).is_file() or not os.access(age_key, os.R_OK)):
                raise ValueError("SOPS_AGE_KEY_FILE is not a readable file")
            if not age_key:
                age_key = str(Path.home() / ".config/sops/age/keys.txt")
            sops_env["SOPS_AGE_KEY_FILE"] = age_key
        try:
            raw = subprocess.run(
                [
                    "sops", "--decrypt", "--input-type", "dotenv", "--output-type", "dotenv",
                    str(env_path),
                ],
                check=True, capture_output=True, text=True, env=sops_env,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("could not decrypt selected environment") from error

    for line in raw.splitlines():
        match = re.match(
            r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line,
        )
        if not match or match.group(1) not in ENV_KEYS:
            continue
        lexer = shlex.shlex(match.group(2), posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        value = " ".join(lexer)
        os.environ.setdefault(match.group(1), value)


def resolve_state_db_path() -> Path:
    """Resolve the state DB from the selected environment's host bind path."""
    host_path = os.environ.get("COVER_STATE_HOST_PATH", "").strip()
    if not host_path:
        raise ValueError("COVER_STATE_HOST_PATH is missing from the selected environment")
    root = Path(host_path)
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("COVER_STATE_HOST_PATH must be an absolute non-root path")
    return root / "state.db"


def resolve_backup_dir() -> Path:
    """Resolve the backup directory from environment or its documented default."""
    raw = os.environ.get("COVER_STATE_BACKUP_HOST_PATH", "/backups/state-db").strip()
    path = Path(raw)
    if not path.is_absolute() or path == Path("/"):
        raise ValueError("COVER_STATE_BACKUP_HOST_PATH must be an absolute non-root path")
    return path


def resolve_cloud_backup_dir() -> str | None:
    remote = os.environ.get("BACKUP_RCLONE_REMOTE", "").strip()
    raw_folder = os.environ.get("BACKUP_RCLONE_FOLDER", "").strip()
    folder = raw_folder.strip("/")
    if not remote and not folder:
        return None
    if (not remote or not folder or ":" in remote or raw_folder.startswith("/")
            or any(part in {"", ".", ".."} for part in folder.split("/"))):
        raise ValueError("BACKUP_RCLONE_REMOTE and BACKUP_RCLONE_FOLDER must identify a remote folder")
    return f"{remote}:{folder}"


def configure_age_key_file(age_key_file: Path | None) -> None:
    """Set an explicit local SOPS key file without exposing key contents."""
    if age_key_file is None:
        return
    if os.environ.get("SOPS_AGE_KEY"):
        raise ValueError("Use either SOPS_AGE_KEY or --age-key-file, not both")
    key_path = age_key_file.expanduser().resolve()
    if not key_path.is_file() or not os.access(key_path, os.R_OK):
        raise ValueError("--age-key-file must point to a readable key file")
    os.environ["SOPS_AGE_KEY_FILE"] = str(key_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="Create a state DB backup")
    backup_parser.add_argument("--db-path", type=Path, help="Override the host state DB path")
    backup_parser.add_argument("--backup-dir", type=Path, help="Override the environment backup directory")
    backup_parser.add_argument("--age-key-file", type=Path, help="SOPS age key file for manual runs")
    backup_parser.add_argument("--retention-days", type=int)
    verify_parser = subparsers.add_parser("verify", help="Restore and check a backup")
    verify_parser.add_argument("backup", type=Path, nargs="?", help="Override the latest backup path")
    verify_parser.add_argument("--age-key-file", type=Path, help="SOPS age key file for manual runs")
    args = parser.parse_args()
    try:
        configure_age_key_file(args.age_key_file)
        if args.command == "backup":
            if args.db_path is None or args.backup_dir is None or args.retention_days is None:
                load_cover_state_environment()
            retention_days = args.retention_days if args.retention_days is not None else int(os.environ.get("COVER_STATE_LOCAL_RETENTION_DAYS", "30"))
            cloud_retention_days = int(os.environ.get("COVER_STATE_CLOUD_RETENTION_DAYS", "90"))
            if retention_days < 1 or cloud_retention_days < 1:
                parser.error("--retention-days must be at least 1")
            db_path = args.db_path or resolve_state_db_path()
            backup_dir = args.backup_dir or resolve_backup_dir()
            cloud_remote = resolve_cloud_backup_dir()
            created = create_backup(db_path, backup_dir, retention_days)
            if cloud_remote:
                copy_cloud_backup(created, cloud_remote, cloud_retention_days)
        else:
            if args.backup is None:
                load_cover_state_environment()
                args.backup = resolve_backup_dir() / "latest.sqlite3"
            verify_backup(args.backup)
    except (OSError, sqlite3.Error, ValueError) as error:
        logger.error("Operation failed: %s", error)
        return 1
    except subprocess.CalledProcessError as error:
        logger.error("rclone operation failed with exit code %s", error.returncode)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
