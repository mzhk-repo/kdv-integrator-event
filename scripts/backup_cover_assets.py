#!/usr/bin/env python3
"""Incrementally copy cover assets to a local backup directory."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from backup_cover_state import configure_age_key_file, load_cover_state_environment, load_server_env_from_host

logger = logging.getLogger("KDV-CoverAssetsBackup")


def backup_assets(source: Path, destination: Path) -> None:
    if not source.is_absolute() or not source.is_dir() or source.is_symlink():
        raise ValueError("Cover assets source must be an absolute, existing directory without symlinks")
    if not destination.is_absolute() or destination == Path("/") or destination.is_symlink():
        raise ValueError("Assets backup destination must be an absolute non-root path without symlinks")
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Assets source and backup directories must not overlap")

    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("Assets backup destination must be a real directory")
    result = subprocess.run(
        ["findmnt", "-T", str(destination), "-n", "-o", "FSTYPE"],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip() in {"fuse.rclone", "rclone"}:
        raise ValueError("Assets backup destination must be local, not an rclone mount")
    subprocess.run(
        ["rsync", "--archive", "--human-readable", "--stats", "--", f"{source}/", f"{destination}/"],
        check=True,
    )
    logger.info("Assets backup complete: %s -> %s", source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-path", type=Path, help="Override the assets source directory")
    parser.add_argument("--backup-dir", type=Path, help="Override the local assets backup directory")
    parser.add_argument("--age-key-file", type=Path, help="SOPS age key file for manual runs")
    args = parser.parse_args()
    try:
        configure_age_key_file(args.age_key_file)
        if args.source_path is None or args.backup_dir is None:
            load_server_env_from_host()
            load_cover_state_environment()
        storage_path = os.environ.get("COVERS_STORAGE_HOST_PATH", "").strip()
        backup_path = os.environ.get("COVER_ASSETS_BACKUP_HOST_PATH", "").strip()
        source = args.source_path or (Path(storage_path) / "assets" if storage_path else None)
        destination = args.backup_dir or (Path(backup_path) if backup_path else None)
        if source is None:
            raise ValueError("COVERS_STORAGE_HOST_PATH is missing from the selected environment")
        if destination is None:
            raise ValueError("COVER_ASSETS_BACKUP_HOST_PATH is missing from the selected environment")
        backup_assets(source, destination)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        logger.error("Assets backup failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
