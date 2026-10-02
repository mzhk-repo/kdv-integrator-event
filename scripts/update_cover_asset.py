#!/usr/bin/env python3
"""Replace one named CDN WebP asset and purge its Cloudflare URL."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

import requests
from PIL import Image


ASSET_NAME = re.compile(r"^[a-f0-9]{64}\.webp$")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _webp_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Source must be a regular, non-symlink file")
    content = path.read_bytes()
    with Image.open(BytesIO(content)) as image:
        if image.format != "WEBP":
            raise ValueError("Source must be a valid WebP image")
        image.load()
    return content


def _write_backup(source: Path, backup: Path) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=backup.parent, prefix="backup-", delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(temporary, backup)
        _sync_directory(backup.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _replace_asset(content: bytes, destination: Path, incoming: Path) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=incoming, prefix="override-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _sync_directory(destination.parent)
        _sync_directory(incoming)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _purge_url(url: str, zone_id: str, api_token: str) -> None:
    response = requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache",
        headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
        json={"files": [url]},
        timeout=30,
    )
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.status_code != 200 or not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Cloudflare URL purge failed (HTTP {response.status_code})")


def _verify_url(url: str, expected: bytes) -> None:
    response = requests.get(url, timeout=30)
    if response.status_code != 200 or response.content != expected:
        raise RuntimeError(f"CDN read-back failed (HTTP {response.status_code})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_name", help="Existing lowercase SHA-256 CDN filename, e.g. <sha>.webp")
    parser.add_argument("source_webp", type=Path, help="Replacement WebP file")
    parser.add_argument("--storage-path", default=(os.environ.get("COVERS_STORAGE_HOST_PATH")
                                                     or os.environ.get("COVERS_STORAGE_PATH")),
                        help="Cover storage root (default: COVERS_STORAGE_HOST_PATH or COVERS_STORAGE_PATH)")
    parser.add_argument("--cdn-base-url", default=os.environ.get("COVERS_CDN_BASE_URL"),
                        help="HTTPS CDN origin (default: COVERS_CDN_BASE_URL)")
    parser.add_argument("--zone-id", default=os.environ.get("CLOUDFLARE_ZONE_ID"),
                        help="Cloudflare zone ID (default: CLOUDFLARE_ZONE_ID)")
    args = parser.parse_args()

    if not ASSET_NAME.fullmatch(args.asset_name):
        parser.error("asset_name must be a lowercase 64-character SHA-256 name ending in .webp")
    if not args.storage_path:
        parser.error("--storage-path or COVERS_STORAGE_PATH is required")
    if not args.cdn_base_url:
        parser.error("--cdn-base-url or COVERS_CDN_BASE_URL is required")
    if not args.zone_id:
        parser.error("--zone-id or CLOUDFLARE_ZONE_ID is required")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", args.zone_id):
        parser.error("Cloudflare zone ID must be 32 hexadecimal characters")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not api_token:
        parser.error("CLOUDFLARE_API_TOKEN is required")

    parsed_url = urlsplit(args.cdn_base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.path not in ("", "/") \
            or parsed_url.query or parsed_url.fragment or parsed_url.username or parsed_url.password:
        parser.error("CDN base URL must be an HTTPS origin without a path, query or credentials")

    root = Path(args.storage_path)
    if not root.is_absolute() or root == Path("/") or root.resolve() != root:
        parser.error("Storage path must be an absolute, non-root, non-symlink path")
    incoming, assets = root / ".incoming", root / "assets"
    if any(not path.is_dir() or path.resolve() != path for path in (root, incoming, assets)):
        parser.error("Storage must contain prepared, non-symlink .incoming and assets directories")
    if incoming.stat().st_dev != assets.stat().st_dev:
        parser.error(".incoming and assets must share a filesystem")

    destination = assets / args.asset_name
    if destination.is_symlink() or not destination.is_file():
        parser.error("Named asset must already exist as a regular, non-symlink file")
    replacement = _webp_bytes(args.source_webp)
    with Image.open(destination) as current:
        if current.format != "WEBP":
            parser.error("Existing named asset is not a valid WebP")
        current.load()

    purge_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{args.asset_name}"
    lock_path = incoming / ".publish.lock"
    backup_dir = incoming / "override-backups"
    if backup_dir.is_symlink():
        parser.error("Override backup directory must not be a symlink")
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    if not backup_dir.is_dir() or backup_dir.resolve() != backup_dir:
        parser.error("Override backup path must be a non-symlink directory")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = backup_dir / f"{args.asset_name}.bak.{stamp}"

    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_backup(destination, backup)
        _replace_asset(replacement, destination, incoming)
        try:
            _purge_url(purge_url, args.zone_id, api_token)
            _verify_url(purge_url, replacement)
        except Exception as error:
            old_content = backup.read_bytes()
            _replace_asset(old_content, destination, incoming)
            try:
                _purge_url(purge_url, args.zone_id, api_token)
            except Exception:
                pass
            raise RuntimeError(
                f"CDN update was not confirmed; original asset restored. Backup retained at {backup}"
            ) from error

    print(f"Updated {purge_url}")
    print(f"Backup: {backup}")
    print(f"Replacement content SHA-256: {hashlib.sha256(replacement).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
