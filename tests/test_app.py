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

from src.app import app


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
