"""Download and normalize explicit Drive covers (Task 4.1)."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Drive URL from MARC 956$p")
    parser.add_argument("--output", required=True, help="Temporary normalized WebP path")
    args = parser.parse_args()
    try:
        result = download_and_normalize(args.source, args.output)
    except Exception:
        parser.exit(1, "Cover download/normalization failed; output is unconfirmed.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
