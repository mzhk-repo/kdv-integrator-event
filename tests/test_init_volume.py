import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "init-volume.sh"


def run_init(covers, state):
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": os.environ["PATH"],
            "COVERS_STORAGE_HOST_PATH": str(covers),
            "COVER_STATE_HOST_PATH": str(state),
        },
        capture_output=True,
        text=True,
    )


def test_init_volume_repairs_modes_and_preserves_files(tmp_path):
    covers, state = tmp_path / "covers", tmp_path / "state"
    assert run_init(covers, state).returncode == 0
    asset = covers / "assets" / "existing.webp"
    database = state / "state.db"
    asset.write_bytes(b"existing asset")
    database.write_bytes(b"existing database")
    asset.chmod(0o640)
    paths = [(covers, 0o755), (covers / "assets", 0o755), (covers / ".incoming", 0o700), (state, 0o700)]
    owners = [(path.stat().st_uid, path.stat().st_gid) for path, _ in paths]
    for path, _ in paths:
        path.chmod(0o777)
    for _ in range(2):
        result = run_init(covers, state)
        assert result.returncode == 0, result.stderr
        assert [path.stat().st_mode & 0o777 for path, _ in paths] == [mode for _, mode in paths]
        assert [(path.stat().st_uid, path.stat().st_gid) for path, _ in paths] == owners
        assert asset.read_bytes() == b"existing asset"
        assert asset.stat().st_mode & 0o777 == 0o640
        assert database.read_bytes() == b"existing database"


@pytest.mark.parametrize("invalid", ["", "relative/path", "/", "/tmp/..", "/tmp/../etc"])
def test_invalid_path_fails_before_creating_other_directory(tmp_path, invalid):
    covers = tmp_path / "covers"
    assert run_init(covers, invalid).returncode != 0
    assert not covers.exists()


@pytest.mark.parametrize("child", ["", "assets", ".incoming"])
def test_symlink_is_rejected_before_changes(tmp_path, child):
    target = tmp_path / "target"
    target.mkdir()
    covers = tmp_path / "covers"
    if child:
        covers.mkdir()
        (covers / child).symlink_to(target, target_is_directory=True)
    else:
        covers.symlink_to(target, target_is_directory=True)
    state = tmp_path / "state"
    assert run_init(covers, state).returncode != 0
    assert not state.exists()
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("child", ["", "assets", ".incoming"])
def test_overlapping_storage_is_rejected(tmp_path, child):
    covers = tmp_path / "covers"
    assert run_init(covers, covers / child).returncode != 0
    assert not covers.exists()


def test_existing_file_is_rejected_before_changes(tmp_path):
    state = tmp_path / "state"
    state.write_bytes(b"keep")
    covers = tmp_path / "covers"
    assert run_init(covers, state).returncode != 0
    assert state.read_bytes() == b"keep"
    assert not covers.exists()


@pytest.mark.parametrize("override", [False, True])
def test_orchestrator_reads_paths_without_sourcing_payload(tmp_path, override):
    orchestrator = SCRIPT.with_name("deploy-orchestrator-swarm.sh").read_text()
    functions = "\n".join(
        name + orchestrator.split(name, 1)[1].split("\n}\n", 1)[0] + "\n}"
        for name in ("read_env_value() {", "run_deploy_adjacent_scripts() {")
    )
    covers, state = tmp_path / "covers with spaces", tmp_path / "state with spaces"
    marker = tmp_path / "must-not-exist"
    payload = tmp_path / "payload.env"
    payload.write_text(
        f'COVERS_STORAGE_HOST_PATH="{covers}"\n'
        f"COVER_STATE_HOST_PATH='{state}'\n"
        f'UNRELATED=$(touch "{marker}")\n'
    )
    env = {"PATH": os.environ["PATH"], "ENV_FILE": str(payload), "SCRIPT_DIR": str(SCRIPT.parent)}
    if override:
        covers, state = tmp_path / "override covers", tmp_path / "override state"
        env.update(COVERS_STORAGE_HOST_PATH=str(covers), COVER_STATE_HOST_PATH=str(state))
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nlog() { :; }\n" + functions + "\nrun_deploy_adjacent_scripts"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (covers / "assets").is_dir()
    assert state.is_dir()
    assert not marker.exists()
