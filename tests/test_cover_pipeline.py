import hashlib
from dataclasses import replace
from unittest.mock import Mock

from PIL import Image
import pytest

from pdf2image.exceptions import PDFPageCountError

from src.services.cover_pipeline import (
    InvalidPDFCoverError, download_and_normalize, normalize_cover, render_pdf_cover,
)
from src.services.sources import GoogleDriveSource, SourceResolver


@pytest.mark.parametrize("size,orientation,expected", [
    ((1200, 800), 1, (600, 400)),
    ((200, 300), 1, (200, 300)),
    ((800, 1200), 6, (600, 400)),
])
def test_normalization(tmp_path, size, orientation, expected):
    source = tmp_path / "source.jpg"
    output = tmp_path / "cover.webp"
    exif = Image.Exif()
    exif[274] = orientation
    exif[270] = "private source metadata"
    Image.new("RGB", size, "blue").save(source, exif=exif, icc_profile=b"private ICC")
    digest = normalize_cover(source, output)
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    with Image.open(output) as image:
        image.load()
        assert image.format == "WEBP"
        assert image.mode == "RGB"
        assert image.size == expected
        assert not image.getexif()
        assert not image.info.get("icc_profile")
    second = tmp_path / "second.webp"
    assert normalize_cover(source, second) == digest


def test_download_sha_checked_before_decode(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (120, 180), (0, 255, 0, 100)).save(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    resolver = SourceResolver(str(tmp_path))
    metadata = {"sha256Checksum": sha}
    resolver.gdrive_source.get_metadata = Mock(return_value=metadata)
    resolved = resolver.resolve_cover("https://drive.google.com/file/d/test-file/view")
    resolved = replace(resolved, local_path=str(source))
    resolver.materialize = Mock(return_value=resolved)
    output = tmp_path / "cover.webp"
    result = download_and_normalize(
        "https://drive.google.com/file/d/test-file/view", output, resolver=resolver
    )
    assert result["source_sha256"] == sha
    assert result["cover_asset_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert resolver.materialize.call_args.kwargs["metadata"] is metadata
    output.unlink()
    source.write_bytes(b"corrupted download")
    with pytest.raises(RuntimeError, match="does not match"):
        download_and_normalize("https://drive.google.com/file/d/test-file/view", output,
                               resolver=resolver, metadata=metadata)
    assert not output.exists()
    resolver.materialize.reset_mock()
    with pytest.raises(ValueError, match="sha256Checksum"):
        download_and_normalize("https://drive.google.com/file/d/test-file/view", output,
                               resolver=resolver, metadata={})
    resolver.materialize.assert_not_called()


def test_invalid_image_and_source_protection(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"not an image")
    output = tmp_path / "cover.webp"
    with pytest.raises(OSError):
        normalize_cover(source, output)
    assert not output.exists()
    Image.new("RGB", (10, 10)).save(source)
    original = source.read_bytes()
    with pytest.raises(ValueError, match="differ"):
        normalize_cover(source, source)
    assert source.read_bytes() == original


def test_real_download_path_with_stub_drive(tmp_path):
    source = tmp_path / "original.png"
    Image.new("RGB", (900, 1200), "red").save(source)
    content = source.read_bytes()
    client = Mock()
    client.get_metadata.return_value = {
        "name": "original.png", "mimeType": "image/png", "size": str(len(content)),
        "sha256Checksum": hashlib.sha256(content).hexdigest(),
    }
    def download(**kwargs):
        from pathlib import Path
        Path(kwargs["destination_path"]).write_bytes(content)
    client.download_to_file.side_effect = download
    drive = GoogleDriveSource(enabled=True, drive_client=client, tmp_dir=str(tmp_path / "download"))
    resolver = SourceResolver(str(tmp_path), gdrive_source=drive)
    output = tmp_path / "normalized.webp"
    result = download_and_normalize("https://drive.google.com/file/d/test-file/view", output,
                                    resolver=resolver)
    client.download_to_file.assert_called_once()
    assert result["source_sha256"] == hashlib.sha256(content).hexdigest()
    with Image.open(output) as image:
        assert image.size == (600, 800)
        assert image.format == "WEBP"


def test_first_pdf_page_uses_canonical_webp_and_cropbox(tmp_path, monkeypatch):
    from src.services import cover_pipeline

    source = tmp_path / 'book.pdf'
    Image.new('RGB', (900, 1200), 'red').save(source, 'PDF')
    output = tmp_path / 'cover.webp'
    original = cover_pipeline.convert_from_path
    convert = Mock(wraps=original)
    monkeypatch.setattr(cover_pipeline, 'convert_from_path', convert)
    digest = render_pdf_cover(source, output)
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='differ'):
        render_pdf_cover(source, source)
    assert convert.call_args.kwargs['first_page'] == convert.call_args.kwargs['last_page'] == 1
    assert convert.call_args.kwargs['use_cropbox'] is True
    with Image.open(output) as image:
        assert image.format == 'WEBP' and image.mode == 'RGB'
        assert image.size == (600, 800)
        assert not image.getexif()


def test_unrenderable_pdf_has_no_output(tmp_path, monkeypatch):
    from src.services import cover_pipeline

    monkeypatch.setattr(cover_pipeline, 'convert_from_path',
                        Mock(side_effect=PDFPageCountError('encrypted PDF')))
    output = tmp_path / 'cover.webp'
    with pytest.raises(InvalidPDFCoverError, match='PDF first page'):
        render_pdf_cover(tmp_path / 'encrypted.pdf', output)
    assert not output.exists()
