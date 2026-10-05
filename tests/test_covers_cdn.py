import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def configure(tmp_path, url, node="examplenode"):
    script = (ROOT / "scripts/deploy-orchestrator-swarm.sh").read_text()
    functions = "\n".join(
        name + script.split(name, 1)[1].split("\n}\n", 1)[0] + "\n}"
        for name in ("read_env_value() {", "configure_covers_cdn() {")
    )
    payload = tmp_path / "env"
    payload.write_text(
        "COVERS_CDN_BASE_URL=https://covers.example.org\n"
        f'COVERS_STORAGE_HOST_PATH="{tmp_path / "cover storage"}"\n'
        'COVERS_STORAGE_PATH="/data/koha-covers"\n'
        f'COVER_STATE_HOST_PATH="{tmp_path / "state storage"}"\n'
    )
    return subprocess.run(
        [
            "bash", "-c",
            "set -euo pipefail\nlog() { :; }\n"
            'docker() { printf "%s" "$TEST_NODE"; }\n'
            + functions + "\nconfigure_covers_cdn\n"
            'printf "%s\\n" "$COVERS_CDN_HOST" "$COVERS_SWARM_NODE_ID" '
            '"$COVERS_STORAGE_HOST_PATH" "$COVER_STATE_HOST_PATH" "$COVERS_NGINX_CONFIG_NAME" "$COVERS_STORAGE_PATH"',
        ],
        env={
            "PATH": os.environ["PATH"],
            "ENV_FILE": str(payload),
            "COVERS_CDN_BASE_URL": url,
            "TEST_NODE": node,
            "STACK_NAME": "teststack",
            "SCRIPT_DIR": str(ROOT / "scripts"),
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("url", ["", "https://other.example.org"])
def test_cdn_routing_uses_selected_url_and_local_storage_node(tmp_path, url):
    result = configure(tmp_path, url)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "other.example.org" if url else "covers.example.org",
        "examplenode", str(tmp_path / "cover storage"), str(tmp_path / "state storage"),
        "teststack_covers_nginx_" + hashlib.sha256((ROOT / "config/covers-cdn/nginx.conf").read_bytes()).hexdigest()[:12],
        "/data/koha-covers",
    ]


@pytest.mark.parametrize("url", [
    "http://covers.example.org", "https://covers.example.org/",
    "https://covers.example.org/path", "https://user:pass@covers.example.org",
    "https://covers.example.org:8443", "https://covers.example.org?q=1",
    "https://covers.example.org#fragment", "https://[invalid", "covers.example.org",
    "https://covers..example.org", "https://covers.example.org\n",
])
def test_invalid_cdn_origin_stops_predeploy(tmp_path, url):
    result = configure(tmp_path, url)
    assert result.returncode != 0
    assert not result.stdout


def test_non_swarm_node_stops_predeploy(tmp_path):
    assert configure(tmp_path, "https://covers.example.org", node="").returncode != 0


def test_rendered_cdn_has_only_readonly_assets_and_private_listener():
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", ".env.example",
            "-f", "docker-compose.yml", "-f", "docker-compose.swarm.yml",
            "config", "--format", "json", "--no-env-resolution",
        ],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "COVERS_CDN_HOST": "covers.example.org",
            "COVERS_SWARM_NODE_ID": "examplenode",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    cdn = services["covers-cdn"]
    api = services["kdv-api"]
    api_mounts = {mount["target"]: mount for mount in api["volumes"]}
    writer = api_mounts["/data/koha-covers"]
    assert not writer.get("read_only")
    assert "node.id == examplenode" in api["deploy"]["placement"]["constraints"]
    assert cdn["user"] == "nginx" and cdn["read_only"]
    assert not any(cdn.get(key) for key in ("ports", "secrets", "environment", "env_file"))
    assert cdn["cap_drop"] == ["ALL"]
    assert not cdn.get("tmpfs")
    mounts = {mount["target"]: mount for mount in cdn["volumes"]}
    assert set(mounts) == {"/tmp", "/usr/share/nginx/html"}
    assert mounts["/tmp"]["type"] == "tmpfs"
    assert int(mounts["/tmp"]["tmpfs"]["size"]) == 16777216
    assert not mounts["/tmp"].get("read_only")
    assert mounts["/usr/share/nginx/html"]["read_only"]
    assert mounts["/usr/share/nginx/html"]["source"].endswith("/assets")
    assert mounts["/usr/share/nginx/html"]["source"] == writer["source"] + "/assets"
    assert cdn["configs"] == [{"source": "covers_nginx", "target": "/etc/nginx/nginx.conf"}]
    assert cdn["deploy"]["placement"]["constraints"] == ["node.id == examplenode"]
    assert cdn["deploy"]["labels"]["traefik.http.routers.kdv-covers.rule"] == "Host(`covers.example.org`)"
    assert cdn["deploy"]["labels"]["traefik.http.services.kdv-covers.loadbalancer.server.port"] == "8080"


def test_swarm_manifest_keeps_writable_tmpfs_mount():
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "-f", "docker-compose.yml",
         "-f", "docker-compose.swarm.yml", "config"],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "COVERS_CDN_HOST": "covers.example.org",
             "COVERS_SWARM_NODE_ID": "examplenode"},
        check=True, capture_output=True, text=True,
    )
    script = (ROOT / "scripts/deploy-orchestrator-swarm.sh").read_text()
    name = "normalize_swarm_manifest() {"
    function = name + script.split(name, 1)[1].split("\n}\n", 1)[0] + "\n}"
    normalized = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + function + "\nnormalize_swarm_manifest"],
        input=rendered.stdout, check=True, capture_output=True, text=True,
    )
    validated = subprocess.run(
        ["docker", "stack", "config", "-c", "-"],
        input=normalized.stdout, check=True, capture_output=True, text=True,
    )
    services = yaml.safe_load(validated.stdout)["services"]
    cdn = services["covers-cdn"]
    api = services["kdv-api"]
    assert "node.id == examplenode" in api["deploy"]["placement"]["constraints"]
    writer = next(mount for mount in api["volumes"] if mount["target"] == "/data/koha-covers")
    assert not writer.get("read_only")
    assert cdn["read_only"] and cdn["user"] == "nginx"
    assert not cdn.get("tmpfs")
    mounts = {mount["target"]: mount for mount in cdn["volumes"]}
    assert mounts["/tmp"]["type"] == "tmpfs"
    assert mounts["/tmp"]["tmpfs"]["size"] == 16777216
    assert not mounts["/tmp"].get("read_only")
    assert mounts["/usr/share/nginx/html"]["read_only"]
    assert mounts["/usr/share/nginx/html"]["source"] == writer["source"] + "/assets"
