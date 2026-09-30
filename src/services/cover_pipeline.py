"""Download, normalize and atomically publish explicit Drive covers."""

import argparse
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageOps

from .sources import SourceResolver


IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
TARGET_WIDTH = 600
WEBP_QUALITY = 82


def verify_drive_download(path, checksum):
    if not checksum:
        raise RuntimeError("Confirmed Drive SHA is required before processing")
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != checksum:
        raise RuntimeError("Downloaded Drive content does not match sha256Checksum")


def normalize_cover(source_path, output_path):
    """Return the hash of a decoded, oriented, metadata-free RGB WebP."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width > TARGET_WIDTH:
                image = image.resize(
                    (TARGET_WIDTH, max(1, round(image.height * TARGET_WIDTH / image.width))),
                    Image.Resampling.LANCZOS,
                )
            # A fresh image keeps source EXIF, ICC and other metadata out of the encoder.
            clean = Image.new("RGB", image.size)
            clean.paste(image)
            with BytesIO() as stream:
                clean.save(stream, "WEBP", quality=WEBP_QUALITY)
                content = stream.getvalue()
    output = Path(output_path)
    if output.resolve() == Path(source_path).resolve():
        raise ValueError("WebP output must differ from the source")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def download_and_normalize(source_url, output_path, *, resolver=None, metadata=None):
    """Reuse Drive authentication/download and verify source bytes before decoding."""
    resolver = resolver or SourceResolver(base_mount_path=str(Path.cwd()))
    source = resolver.resolve_cover(source_url)
    if source is None or source.source_type != "gdrive":
        raise ValueError("A Google Drive cover URL is required")
    if metadata is None:
        metadata = resolver.gdrive_source.get_metadata(
            source.diagnostics["file_id"], source.diagnostics.get("resource_key")
        )
    checksum = metadata.get("sha256Checksum")
    if not isinstance(checksum, str) or len(checksum) != 64 or any(
        char not in "0123456789abcdef" for char in checksum
    ):
        raise ValueError("A valid Drive sha256Checksum is required")
    source = resolver.materialize(
        source, metadata=metadata, allowed_mime_types=IMAGE_MIME_TYPES
    )
    verify_drive_download(source.local_path, checksum)
    asset_sha = normalize_cover(source.local_path, output_path)
    return {"file": str(output_path), "source_sha256": checksum, "cover_asset_sha256": asset_sha}


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_cover(webp_path, storage_path=None, *, expected_sha256=None):
    """Atomically publish immutable WebP bytes in prepared, node-local storage."""
    configured = storage_path if storage_path is not None else os.environ.get("COVERS_STORAGE_PATH")
    if not configured:
        raise ValueError("COVERS_STORAGE_PATH is required")
    root = Path(configured)
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("Cover storage must be an absolute non-root directory")
    incoming, assets = root / ".incoming", root / "assets"
    for directory in (root, incoming, assets):
        if not directory.is_dir() or directory.resolve() != directory:
            raise ValueError("Cover storage must contain prepared, non-symlink directories")
    if incoming.stat().st_dev != assets.stat().st_dev:
        raise ValueError("Incoming and assets must share the same filesystem")

    content = Path(webp_path).read_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(content)) as image:
            if image.format != "WEBP":
                raise ValueError("Only valid WebP assets can be published")
            image.load()
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError("Normalized WebP SHA does not match expected asset SHA")
    destination = assets / f"{digest}.webp"

    # ponytail: serial publications; use per-asset locks if publish throughput requires it.
    descriptor = os.open(incoming / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Every publisher holds this lock: these names can only be abandoned writes.
        for stale in incoming.glob("publish-*.tmp"):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink()
        if destination.is_symlink():
            raise ValueError("Asset destination must not be a symlink")
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != content:
                raise ValueError("Existing immutable asset has unexpected content")
            _sync_directory(assets)
            _sync_directory(incoming)
            return {"file": str(destination), "cover_asset_sha256": digest}

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=incoming, prefix="publish-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            _sync_directory(assets)
            _sync_directory(incoming)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"file": str(destination), "cover_asset_sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Drive URL from MARC 956$p")
    parser.add_argument("--output", required=True, help="Temporary normalized WebP path")
    parser.add_argument("--publish", action="store_true", help="Publish the normalized WebP atomically")
    parser.add_argument("--storage-path", help="Prepared storage root; defaults to COVERS_STORAGE_PATH")
    args = parser.parse_args()
    try:
        result = download_and_normalize(args.source, args.output)
        if args.publish:
            result.update(publish_cover(args.output, args.storage_path,
                                       expected_sha256=result["cover_asset_sha256"]))
    except Exception:
        parser.exit(1, "Cover pipeline failed; output is unconfirmed.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
