#!/usr/bin/env python3
"""Create and restore-check SQLite cover state backups."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("KDV-CoverStateBackup")
BACKUP_NAME = re.compile(r"^state-(\d{8}T\d{12}Z)-([0-9]+)\.sqlite3$")


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
        cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
        for candidate in backup_dir.iterdir():
            match = BACKUP_NAME.fullmatch(candidate.name)
            if match and not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        directory_fd = os.open(backup_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        logger.info("Backup complete: %s records=%s", final_path, records)
        return final_path
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup", help="Create a state DB backup")
    backup_parser.add_argument("--db-path", type=Path, default=os.environ.get("COVER_STATE_DB_PATH"))
    backup_parser.add_argument("--backup-dir", type=Path, default=Path("/backups/state-db"))
    backup_parser.add_argument("--retention-days", type=int, default=30)
    verify_parser = subparsers.add_parser("verify", help="Restore and check a backup")
    verify_parser.add_argument("backup", type=Path, nargs="?", default=Path("/backups/state-db/latest.sqlite3"))
    args = parser.parse_args()
    try:
        if args.command == "backup":
            if args.db_path is None:
                parser.error("set COVER_STATE_DB_PATH or pass --db-path")
            if args.retention_days < 1:
                parser.error("--retention-days must be at least 1")
            create_backup(args.db_path, args.backup_dir, args.retention_days)
        else:
            verify_backup(args.backup)
    except (OSError, sqlite3.Error, ValueError) as error:
        logger.error("Operation failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
