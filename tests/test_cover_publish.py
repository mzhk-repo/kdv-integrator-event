from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys

from PIL import Image
import pytest

from src.services.cover_pipeline import publish_cover
from src.services import cover_pipeline


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "storage"
    (root / "assets").mkdir(parents=True)
    (root / ".incoming").mkdir(mode=0o700)
    return root


def webp(tmp_path, name="source.webp", color="blue"):
    path = tmp_path / name
    Image.new("RGB", (600, 800), color).save(path, "WEBP", quality=82)
    return path


def test_dedup_concurrent_publications_preserve_asset(tmp_path, storage):
    source = webp(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    first = publish_cover(source, storage, expected_sha256=digest)
    asset = Path(first["file"])
    before = asset.stat()
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: publish_cover(source, storage), range(16)))
    assert all(result == first for result in results)
    assert asset.name == f"{digest}.webp"
    assert list((storage / "assets").iterdir()) == [asset]
    assert asset.read_bytes() == source.read_bytes()
    assert asset.stat().st_ino == before.st_ino
    assert asset.stat().st_mtime_ns == before.st_mtime_ns
    assert asset.stat().st_mode & 0o777 == 0o644
    assert not list((storage / ".incoming").glob("*.tmp"))


def test_existing_corruption_and_symlink_fail_closed(tmp_path, storage):
    source = webp(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = storage / "assets" / f"{digest}.webp"
    destination.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="unexpected content"):
        publish_cover(source, storage)
    assert destination.read_bytes() == b"corrupt"
    destination.unlink()
    destination.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        publish_cover(source, storage)


def test_invalid_inputs_and_storage(tmp_path, storage, monkeypatch):
    source = webp(tmp_path)
    monkeypatch.delenv("COVERS_STORAGE_PATH", raising=False)
    with pytest.raises(ValueError, match="required"):
        publish_cover(source)
    for root in ("relative", "/", tmp_path / "missing"):
        with pytest.raises(ValueError):
            publish_cover(source, root)
    link = tmp_path / "link"
    link.symlink_to(storage, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        publish_cover(source, link)
    with pytest.raises(ValueError, match="expected asset SHA"):
        publish_cover(source, storage, expected_sha256="0" * 64)
    source.write_bytes(b"RIFFbroken")
    with pytest.raises(OSError):
        publish_cover(source, storage)
    Image.new("RGB", (10, 10)).save(source, "PNG")
    with pytest.raises(ValueError, match="WebP"):
        publish_cover(source, storage)
    assert not list((storage / "assets").iterdir())


def test_cross_filesystem_rejected_before_write(tmp_path, storage, monkeypatch):
    source = webp(tmp_path)
    real_stat = Path.stat
    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == storage / "assets":
            values = list(result)
            values[2] += 1  # st_dev
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(ValueError, match="same filesystem"):
        publish_cover(source, storage)
    assert not list((storage / ".incoming").iterdir())


def test_replace_failure_cleans_own_temporary_file(tmp_path, storage, monkeypatch):
    source = webp(tmp_path)
    def fail_replace(*args):
        raise OSError("simulated rename failure")
    monkeypatch.setattr("src.services.cover_pipeline.os.replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        publish_cover(source, storage)
    assert not list((storage / "assets").iterdir())
    assert not list((storage / ".incoming").glob("*.tmp"))


def test_sync_failure_does_not_publish(tmp_path, storage, monkeypatch):
    source = webp(tmp_path)
    def fail_sync(descriptor):
        raise OSError("simulated disk sync failure")
    monkeypatch.setattr("src.services.cover_pipeline.os.fsync", fail_sync)
    with pytest.raises(OSError, match="disk sync failure"):
        publish_cover(source, storage)
    assert not list((storage / "assets").iterdir())
    assert not list((storage / ".incoming").glob("*.tmp"))


def test_cli_publishes_normalized_result_using_environment(tmp_path, storage, monkeypatch, capsys):
    source = webp(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv("COVERS_STORAGE_PATH", str(storage))
    monkeypatch.setattr(cover_pipeline, "download_and_normalize", lambda *args: {
        "file": str(source), "source_sha256": "a" * 64, "cover_asset_sha256": digest,
    })
    monkeypatch.setattr(sys, "argv", ["cover_pipeline", "--source", "stub-drive", "--output", str(source), "--publish"])
    cover_pipeline.main()
    import json
    result = json.loads(capsys.readouterr().out)
    assert result["source_sha256"] == "a" * 64
    assert result["cover_asset_sha256"] == digest
    assert Path(result["file"]).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("phase", ["write", "before_replace", "after_replace"])
def test_sigkill_keeps_final_assets_complete_and_retry_cleans_staging(tmp_path, storage, phase):
    old = publish_cover(webp(tmp_path, "old.webp", "red"), storage)
    old_bytes = Path(old["file"]).read_bytes()
    source = webp(tmp_path)
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    script = r'''
import os, signal, sys
from src.services import cover_pipeline as pipeline
phase, source, storage = sys.argv[1:]
real_temporary = pipeline.tempfile.NamedTemporaryFile
real_replace = pipeline.os.replace
class InterruptedWriter:
    def __init__(self, stream): self.stream = stream
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def __getattr__(self, name): return getattr(self.stream, name)
    def write(self, content):
        self.stream.write(content[:len(content) // 2])
        self.stream.flush()
        os.kill(os.getpid(), signal.SIGKILL)
def replace(source, destination):
    if phase == "after_replace": real_replace(source, destination)
    os.kill(os.getpid(), signal.SIGKILL)
if phase == "write":
    pipeline.tempfile.NamedTemporaryFile = lambda **kw: InterruptedWriter(real_temporary(**kw))
else:
    pipeline.os.replace = replace
pipeline.publish_cover(source, storage)
'''
    child = subprocess.run([sys.executable, "-c", script, phase, str(source), str(storage)],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
    assert child.returncode == -signal.SIGKILL, child.stderr.decode()
    destination = storage / "assets" / f"{digest}.webp"
    if phase == "after_replace":
        assert destination.read_bytes() == content
    else:
        assert not destination.exists()
        temporary = list((storage / ".incoming").glob("publish-*.tmp"))
        assert len(temporary) == 1
        if phase == "write":
            assert temporary[0].read_bytes() == content[:len(content) // 2]
    assert Path(old["file"]).read_bytes() == old_bytes
    assert not list((storage / "assets").glob("*.tmp"))
    assert publish_cover(source, storage)["file"] == str(destination)
    assert destination.read_bytes() == content
    assert not list((storage / ".incoming").glob("publish-*.tmp"))
