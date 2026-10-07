"""Remove old cover assets that are no longer referenced by cover state."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger("KDV-CoverGC")
ASSET_NAME = re.compile(r"^[0-9a-f]{64}\.webp$")
MIN_RETENTION_SECONDS = 86400


def load_gc_environment() -> None:
    """Load only the selected env file; do not fall back across environments."""
    selected = os.environ.get("ORCHESTRATOR_ENV_FILE", "")
    if selected:
        env_path = Path(selected)
        if not env_path.is_file():
            raise ValueError("ORCHESTRATOR_ENV_FILE does not exist")
        raw = env_path.read_text(encoding="utf-8")
    else:
        environment = os.environ.get("SERVER_ENV", "").strip().lower()
        normalized = {"dev": "dev", "development": "dev", "prod": "prod", "production": "prod"}.get(environment)
        if not normalized:
            raise ValueError("SERVER_ENV must identify dev or prod")
        env_path = Path(__file__).resolve().parents[2] / f"env.{normalized}.enc"
        if not env_path.is_file() or not shutil.which("sops"):
            raise ValueError(f"encrypted environment file or sops unavailable for {normalized}")
        command = ["sops", "--decrypt", "--input-type", "dotenv", "--output-type", "dotenv"]
        # SOPS_AGE_KEY is injected by CI. For local keys, SOPS reads
        # SOPS_AGE_KEY_FILE from its environment (there is no key-file CLI flag).
        sops_env = os.environ.copy()
        if sops_env.get("SOPS_AGE_KEY"):
            sops_env.pop("SOPS_AGE_KEY_FILE", None)
        else:
            age_key = sops_env.get("SOPS_AGE_KEY_FILE")
            if age_key:
                if not Path(age_key).is_file() or not os.access(age_key, os.R_OK):
                    raise ValueError("SOPS_AGE_KEY_FILE is not a readable file")
            else:
                age_key = str(Path.home() / ".config/sops/age/keys.txt")
            sops_env["SOPS_AGE_KEY_FILE"] = age_key
        command.append(str(env_path))
        try:
            raw = subprocess.run(command, check=True, capture_output=True, text=True, env=sops_env).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("could not decrypt selected environment") from error
    for key, value in dotenv_values(stream=StringIO(raw)).items():
        if value is not None:
            os.environ.setdefault(key, value)


def collect_garbage(*, db_path: str, storage_path: str, retention_seconds: int,
                    dry_run: bool = True, now: float | None = None) -> list[str]:
    """List or delete old unreferenced assets; dry-run is the default."""
    if not db_path or not Path(db_path).is_absolute():
        raise ValueError("COVER_STATE_DB_PATH must be an absolute file path")
    root = Path(storage_path)
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("Cover storage must be an absolute non-root directory")
    incoming, assets = root / ".incoming", root / "assets"
    for directory in (root, incoming, assets):
        if not directory.is_dir() or directory.resolve() != directory:
            raise ValueError("Cover storage must contain prepared, non-symlink directories")
    if incoming.stat().st_dev != assets.stat().st_dev:
        raise ValueError("Incoming and assets must share a filesystem")
    if type(retention_seconds) is not int or retention_seconds < MIN_RETENTION_SECONDS:
        raise ValueError(f"retention_seconds must be an integer >= {MIN_RETENTION_SECONDS}")

    # The publisher uses this same lock; acquire it before reading references so
    # publication and deletion cannot cross each other's state/file operations.
    descriptor = os.open(incoming / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    removed: list[str] = []
    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as connection:
            references = {
                row[0] for row in connection.execute(
                    "SELECT DISTINCT cover_asset_sha256 FROM records "
                    "WHERE cover_asset_sha256 IS NOT NULL AND cover_asset_sha256 != ''"
                )
            }
        cutoff = (time.time() if now is None else now) - retention_seconds
        for asset in assets.iterdir():
            if not ASSET_NAME.fullmatch(asset.name) or asset.is_symlink() or not asset.is_file():
                continue
            if asset.stem in references or asset.stat().st_mtime >= cutoff:
                continue
            logger.info("%s unreferenced cover asset %s",
                        "Would remove" if dry_run else "Removing", asset.name)
            if not dry_run:
                asset.unlink()
                removed.append(asset.name)
    return removed


def main() -> int:
    try:
        load_gc_environment()
    except (OSError, ValueError) as error:
        logger.error("Environment load failed: %s", error)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=os.environ.get("COVER_STATE_DB_PATH"))
    parser.add_argument("--storage-path", default=os.environ.get("COVERS_STORAGE_PATH"))
    parser.add_argument("--retention-days", type=int,
                        default=int(os.environ.get("COVER_ASSET_RETENTION_DAYS", "90")))
    parser.add_argument("--apply", action="store_true", help="Delete eligible assets (default: dry-run)")
    args = parser.parse_args()
    if not args.db_path:
        parser.error("set COVER_STATE_DB_PATH or pass --db-path")
    if not args.storage_path:
        parser.error("set COVERS_STORAGE_PATH or pass --storage-path")
    try:
        removed = collect_garbage(
            db_path=args.db_path,
            storage_path=args.storage_path,
            retention_seconds=args.retention_days * 86400,
            dry_run=not args.apply,
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        logger.error("GC stopped: %s", error)
        return 1
    print(f"GC {'applied' if args.apply else 'dry-run'}; removed={len(removed)}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
