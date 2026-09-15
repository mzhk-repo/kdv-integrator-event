import os

# Provide required config env vars before importing src.app
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

from src.app import _EXPORT_RUN_LOCK, _run_export_task, app


def test_health_endpoint_is_public_and_ok():
    client = app.test_client()
    response = client.get("/kdv/api/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_ready_endpoint_returns_200_when_mount_is_rw(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.INTEGRATOR_MOUNT_PATH", "/tmp")
    monkeypatch.setattr("src.app.os.path.isdir", lambda _p: True)
    monkeypatch.setattr("src.app.os.access", lambda _p, _mode: True)

    response = client.get("/kdv/api/ready")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"


def test_ready_endpoint_returns_503_when_mount_is_missing(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.INTEGRATOR_MOUNT_PATH", "/mnt/missing")
    monkeypatch.setattr("src.app.os.path.isdir", lambda _p: False)

    response = client.get("/kdv/api/ready")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "not_ready"
    assert payload["mount_exists"] is False


def test_legacy_auth_rejects_without_token(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    response = client.get("/kdv/api/status/fake-task-id")

    assert response.status_code == 401


def test_legacy_auth_accepts_valid_token(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")
    response = client.get(
        "/kdv/api/status/fake-task-id", headers={"X-KDV-TOKEN": "test-token"}
    )

    # Endpoint works and falls through to app logic (task not found is expected)
    assert response.status_code == 404


def test_dual_auth_accepts_cloudflare_header(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "dual")
    monkeypatch.setattr("src.app._verify_cf_access_jwt", lambda _token: True)

    response = client.get(
        "/kdv/api/status/fake-task-id",
        headers={"Cf-Access-Jwt-Assertion": "dummy"},
    )

    assert response.status_code == 404


def test_dual_auth_accepts_cloudflare_cookie(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "dual")
    monkeypatch.setattr("src.app._verify_cf_access_jwt", lambda _token: True)

    client.set_cookie("CF_Authorization", "dummy-cookie-jwt")
    response = client.get("/kdv/api/status/fake-task-id")

    assert response.status_code == 404


def test_cors_allowlist_returns_origin_only_for_allowed(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.CORS_ALLOWLIST", {"https://koha.example.org"})

    allowed = client.get(
        "/kdv/api/health", headers={"Origin": "https://koha.example.org"}
    )
    denied = client.get("/kdv/api/health", headers={"Origin": "https://evil.example"})

    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://koha.example.org"
    assert denied.headers.get("Access-Control-Allow-Origin") is None


def test_integrate_without_payload_defaults_to_optimization(monkeypatch):
    client = app.test_client()
    captured = {}

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")
    monkeypatch.setattr("src.app._make_clients", lambda: (object(), object()))

    def fake_start_task(func, biblionumber, **kwargs):
        captured["func"] = func
        captured["biblionumber"] = biblionumber
        captured["kwargs"] = kwargs
        return "task-1"

    monkeypatch.setattr("src.app.task_manager.start_task", fake_start_task)

    response = client.post(
        "/kdv/api/integrate/123", headers={"X-KDV-TOKEN": "test-token"}
    )

    assert response.status_code == 202
    assert response.get_json()["task_id"] == "task-1"
    assert captured["biblionumber"] == 123
    assert captured["kwargs"]["skip_optimization"] is False


def test_integrate_accepts_skip_optimization_true(monkeypatch):
    client = app.test_client()
    captured = {}

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")
    monkeypatch.setattr("src.app._make_clients", lambda: (object(), object()))

    def fake_start_task(func, biblionumber, **kwargs):
        captured["kwargs"] = kwargs
        return "task-2"

    monkeypatch.setattr("src.app.task_manager.start_task", fake_start_task)

    response = client.post(
        "/kdv/api/integrate/123",
        json={"skip_optimization": True},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 202
    assert response.get_json()["task_id"] == "task-2"
    assert captured["kwargs"]["skip_optimization"] is True


def test_robot_batch_rejects_without_token(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    response = client.post("/kdv/api/robot/batch", json={"candidates": "100"})

    assert response.status_code == 401


def test_robot_batch_accepts_valid_payload(monkeypatch):
    client = app.test_client()
    captured = {}

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")

    def fake_start_task(func, candidates_text, **kwargs):
        captured["func"] = func
        captured["candidates_text"] = candidates_text
        captured["kwargs"] = kwargs
        return "robot-task-1"

    monkeypatch.setattr("src.app.task_manager.start_task", fake_start_task)

    response = client.post(
        "/kdv/api/robot/batch",
        json={
            "candidates": "100-102, 101",
            "skip_optimization": True,
            "parallelism": 2,
            "max_wait": 1200,
        },
        headers={"X-KDV-TOKEN": "test-token"},
    )

    payload = response.get_json()
    assert response.status_code == 202
    assert payload["task_id"] == "robot-task-1"
    assert payload["candidates_count"] == 3
    assert payload["preview"] == ["100", "101", "102"]
    assert captured["candidates_text"] == "100-102, 101"
    assert captured["kwargs"] == {
        "skip_optimization": True,
        "parallelism": 2,
        "max_wait": 1200,
    }


def test_robot_batch_rejects_empty_candidates(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")

    response = client.post(
        "/kdv/api/robot/batch",
        json={"candidates": "   "},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "candidates is required"


def test_robot_batch_rejects_invalid_candidates(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")

    response = client.post(
        "/kdv/api/robot/batch",
        json={"candidates": "abc, nope"},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "No valid candidates found"


def test_robot_batch_rejects_invalid_controls(monkeypatch):
    client = app.test_client()

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")

    response = client.post(
        "/kdv/api/robot/batch",
        json={"candidates": "100", "parallelism": 0, "max_wait": 900},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "parallelism must be >= 1"

    response = client.post(
        "/kdv/api/robot/batch",
        json={"candidates": "100", "parallelism": 1, "max_wait": 29},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "max_wait must be >= 30"


def test_export_run_accepts_file_links_options(monkeypatch):
    client = app.test_client()
    captured = {}

    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")
    monkeypatch.setattr("src.app.ExportConfig.from_env", lambda: type("Config", (), {"enabled": True})())

    def fake_start_task(func, options):
        captured["func"] = func
        captured["options"] = options
        return "export-task-1"

    monkeypatch.setattr("src.app.task_manager.start_task", fake_start_task)
    try:
        response = client.post(
            "/kdv/api/export/run",
            json={
                "biblionumber_from": 100,
                "biblionumber_to": 200,
                "export_mode": "file-links",
            },
            headers={"X-KDV-TOKEN": "test-token"},
        )
    finally:
        _EXPORT_RUN_LOCK.release()

    assert response.status_code == 202
    assert response.get_json()["task_id"] == "export-task-1"
    assert captured["func"] is _run_export_task
    assert captured["options"].dry_run is False
    assert captured["options"].biblionumber_from == 100
    assert captured["options"].biblionumber_to == 200
    assert captured["options"].export_mode == "file-links"
    assert captured["options"].manual_export is True
    assert captured["options"].send_email is False


def test_export_run_rejects_invalid_range(monkeypatch):
    client = app.test_client()
    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")

    response = client.post(
        "/kdv/api/export/run",
        json={"biblionumber_from": 200, "biblionumber_to": 100},
        headers={"X-KDV-TOKEN": "test-token"},
    )

    assert response.status_code == 400


def test_export_run_rejects_parallel_run(monkeypatch):
    client = app.test_client()
    monkeypatch.setattr("src.app.KDV_AUTH_MODE", "legacy")
    monkeypatch.setattr("src.app.KDV_API_TOKEN", "test-token")
    monkeypatch.setattr("src.app.ExportConfig.from_env", lambda: type("Config", (), {"enabled": True})())
    assert _EXPORT_RUN_LOCK.acquire(blocking=False)
    try:
        response = client.post(
            "/kdv/api/export/run",
            json={"biblionumber_from": 100, "biblionumber_to": 200},
            headers={"X-KDV-TOKEN": "test-token"},
        )
    finally:
        _EXPORT_RUN_LOCK.release()

    assert response.status_code == 409
