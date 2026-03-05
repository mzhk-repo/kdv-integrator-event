import os
from src.services.files import FileService
from src.services.covers import CoverService


def test_files_version_and_move(tmp_path):
    # create a fake pdf file
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    orig = source_dir / "test.pdf"
    orig.write_text("dummy")
    # run service
    fs = FileService()
    new_path = fs.version_and_move(str(orig), 123)
    assert os.path.exists(new_path)
    assert "biblio_123_v01.pdf" in new_path

    # second file should increment version
    orig2 = source_dir / "test2.pdf"
    orig2.write_text("dummy2")
    new_path2 = fs.version_and_move(str(orig2), 123)
    assert os.path.exists(new_path2)
    assert "v02" in new_path2


def test_files_move_to_error(tmp_path):
    # simulate a processed folder and file
    base = tmp_path / "base"
    processed = base / "Processed"
    processed.mkdir(parents=True)
    file_path = processed / "biblio_1_v01.pdf"
    file_path.write_text("data")
    fs = FileService()
    fs.move_to_error(str(file_path))
    error_dir = base / "Error"
    assert error_dir.exists()
    moved = error_dir / "biblio_1_v01.pdf"
    assert moved.exists()


def test_cover_service_initialization():
    # just ensure it can be constructed without Koha
    cs = CoverService()
    assert hasattr(cs, "process_book")
