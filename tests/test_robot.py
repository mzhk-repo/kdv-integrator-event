import importlib
import os

import pytest

os.environ.setdefault("KDV_API_TOKEN", "test-token")
os.environ.setdefault("KOHA_API_URL", "http://koha.local")
os.environ.setdefault("KOHA_OPAC_URL", "http://koha.local")
os.environ.setdefault("KOHA_API_USER", "user")
os.environ.setdefault("KOHA_API_PASS", "pass")
os.environ.setdefault("DSPACE_API_URL", "http://dspace.local/server")
os.environ.setdefault("DSPACE_UI_URL", "http://dspace.local")
os.environ.setdefault("DSPACE_API_USER", "user")
os.environ.setdefault("DSPACE_API_PASS", "pass")
os.environ.setdefault("INTEGRATOR_MOUNT_PATH", "/tmp")

import scripts.robot as robot


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_robot_help_lists_cli_arguments(capsys):
    parser = robot.build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--skip-optimization" in help_text
    assert "--parallelism" in help_text
    assert "--max-wait" in help_text


def test_robot_skip_optimization_flag_sets_payload(monkeypatch):
    captured = {}

    def fake_post(_url, headers=None, json=None):
        captured["json"] = json
        return FakeResponse(202, {"task_id": "task-1"})

    def fake_get(_url, headers=None):
        return FakeResponse(200, {"status": "success", "result": {"handle": "h"}})

    monkeypatch.setattr(robot.requests, "post", fake_post)
    monkeypatch.setattr(robot.requests, "get", fake_get)
    monkeypatch.setattr(robot.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(robot, "POLL_INTERVAL", 0)

    result = robot.process_single_biblio("123", skip_optimization=True, max_wait=1)

    assert result == "SUCCESS"
    assert captured["json"] == {"skip_optimization": True}


def test_robot_default_payload_keeps_optimization_enabled(monkeypatch):
    captured = {}

    def fake_post(_url, headers=None, json=None):
        captured["json"] = json
        return FakeResponse(202, {"task_id": "task-2"})

    def fake_get(_url, headers=None):
        return FakeResponse(200, {"status": "success", "result": {"handle": "h"}})

    monkeypatch.setattr(robot.requests, "post", fake_post)
    monkeypatch.setattr(robot.requests, "get", fake_get)
    monkeypatch.setattr(robot.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(robot, "POLL_INTERVAL", 0)

    result = robot.process_single_biblio("123", max_wait=1)

    assert result == "SUCCESS"
    assert captured["json"] == {"skip_optimization": False}


def test_robot_parallelism_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("ROBOT_PARALLELISM", "2")
    reloaded_robot = importlib.reload(robot)

    args = reloaded_robot.build_parser().parse_args(["candidates.txt"])

    assert args.parallelism == 2
    monkeypatch.delenv("ROBOT_PARALLELISM", raising=False)
    importlib.reload(reloaded_robot)


def test_robot_warns_when_parallel_optimization_queue(caplog):
    with caplog.at_level("WARNING", logger="Robot"):
        robot.warn_optimizer_queue_if_needed(
            parallelism=2, skip_optimization=False, max_wait=900
        )

    assert "ROBOT_PARALLELISM > 1" in caplog.text
    assert "--skip-optimization" in caplog.text


def test_parse_candidates_text_supports_candidates_syntax():
    candidates = """
    # comment
    105
    100-102, 101, 110
    205-203
    bad-token
    """

    assert robot.parse_candidates_text(candidates) == [
        "100",
        "101",
        "102",
        "105",
        "110",
        "203",
        "204",
        "205",
    ]


def test_robot_run_batch_returns_stats(monkeypatch):
    processed = []

    def fake_process_single_biblio(biblionumber, skip_optimization=False, max_wait=None):
        processed.append((biblionumber, skip_optimization, max_wait))
        return "SUCCESS" if biblionumber == "100" else "SKIPPED"

    monkeypatch.setattr(robot, "process_single_biblio", fake_process_single_biblio)
    monkeypatch.setattr(robot.time, "sleep", lambda _seconds: None)

    stats = robot.run_batch_ids(
        ["100", "101"],
        skip_optimization=True,
        parallelism=1,
        max_wait=45,
    )

    assert stats["SUCCESS"] == 1
    assert stats["SKIPPED"] == 1
    assert processed == [("100", True, 45), ("101", True, 45)]
