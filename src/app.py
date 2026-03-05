import logging
import os
from flask import Flask, jsonify, request, abort

try:
    import jwt
except ImportError:  # pragma: no cover - handled by config/mode checks
    jwt = None

from .tasks import task_manager
from .config import (
    setup_logging,
    KDV_API_TOKEN,
    KDV_AUTH_MODE,
    KDV_CORS_ALLOWLIST,
    CF_ACCESS_TEAM_DOMAIN,
    CF_ACCESS_AUD,
    INTEGRATOR_MOUNT_PATH,
    KOHA_OPAC_URL,
)
from .core import process_integration_logic, parse_marc_details

# wrappers imported here for DI in web handlers
from .clients.koha import KohaClientWrapper
from .clients.dspace import DSpaceClientWrapper

setup_logging()
logger = logging.getLogger("KDV-Core")

app = Flask(__name__)


def _normalize_cf_team_domain(raw: str) -> str:
    val = (raw or "").strip()
    if val.startswith("https://"):
        val = val[len("https://") :]
    elif val.startswith("http://"):
        val = val[len("http://") :]
    return val.rstrip("/")


def _parse_allowlist(raw: str):
    # Keep only normalized non-empty origins.
    return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}


CORS_ALLOWLIST = _parse_allowlist(KDV_CORS_ALLOWLIST) or {KOHA_OPAC_URL.rstrip("/")}
CF_TEAM_DOMAIN = _normalize_cf_team_domain(CF_ACCESS_TEAM_DOMAIN)


def _origin_is_allowed(origin: str) -> bool:
    if not origin:
        return False
    return origin.rstrip("/") in CORS_ALLOWLIST


def _verify_cf_access_jwt(token: str) -> bool:
    if not token:
        return False
    if not CF_TEAM_DOMAIN or not CF_ACCESS_AUD:
        return False
    if jwt is None:
        logger.error("PyJWT is required for Cloudflare Access auth mode")
        return False

    certs_url = f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs"
    issuer = f"https://{CF_TEAM_DOMAIN}"

    try:
        signing_key = jwt.PyJWKClient(certs_url).get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=CF_ACCESS_AUD,
            issuer=issuer,
        )
        return True
    except Exception as exc:
        logger.warning(f"Cloudflare Access JWT rejected: {exc}")
        return False


def _is_authorized() -> bool:
    token_ok = request.headers.get("X-KDV-TOKEN") == KDV_API_TOKEN
    cf_header_token = request.headers.get("Cf-Access-Jwt-Assertion", "")
    cf_cookie_token = request.cookies.get("CF_Authorization", "")
    cf_ok = _verify_cf_access_jwt(cf_header_token) or _verify_cf_access_jwt(cf_cookie_token)

    if KDV_AUTH_MODE == "legacy":
        if not token_ok:
            logger.warning("Auth denied: legacy mode and invalid/missing X-KDV-TOKEN")
        return token_ok
    if KDV_AUTH_MODE == "dual":
        if not (token_ok or cf_ok):
            logger.warning(
                "Auth denied: dual mode (token_ok=%s, cf_header=%s, cf_cookie=%s, team_domain=%s)",
                token_ok,
                bool(cf_header_token),
                bool(cf_cookie_token),
                CF_TEAM_DOMAIN,
            )
        return token_ok or cf_ok
    if KDV_AUTH_MODE == "cf-only":
        if not cf_ok:
            logger.warning(
                "Auth denied: cf-only mode (cf_header=%s, cf_cookie=%s, team_domain=%s)",
                bool(cf_header_token),
                bool(cf_cookie_token),
                CF_TEAM_DOMAIN,
            )
        return cf_ok

    logger.error(f"Invalid KDV_AUTH_MODE='{KDV_AUTH_MODE}', expected legacy|dual|cf-only")
    return False


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if _origin_is_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-KDV-TOKEN, Authorization, Cf-Access-Jwt-Assertion"
    )
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


@app.before_request
def check_security():
    if request.path.endswith(("/health", "/ready")) or request.method == "OPTIONS":
        return
    if not _is_authorized():
        abort(401, description="Unauthorized")


@app.route("/kdv/api/health", methods=["GET"])
def healthcheck():
    return jsonify({"status": "ok", "mode": "v6.5-parallel-covers"})


@app.route("/kdv/api/ready", methods=["GET"])
def readinesscheck():
    mount_exists = os.path.isdir(INTEGRATOR_MOUNT_PATH)
    mount_rw = os.access(INTEGRATOR_MOUNT_PATH, os.R_OK | os.W_OK) if mount_exists else False

    if mount_exists and mount_rw:
        return jsonify({"status": "ready", "mount_path": INTEGRATOR_MOUNT_PATH}), 200

    return (
        jsonify(
            {
                "status": "not_ready",
                "mount_path": INTEGRATOR_MOUNT_PATH,
                "mount_exists": mount_exists,
                "mount_read_write": mount_rw,
            }
        ),
        503,
    )


def _make_clients():
    """Return a fresh pair of Koha/DSpace clients (wrappers) for glue code.

    In tests we can monkeypatch this function to return stubs.
    """
    return KohaClientWrapper(), DSpaceClientWrapper()


@app.route("/kdv/api/integrate/<int:biblionumber>", methods=["POST"])
def archive_record_async(biblionumber):
    try:
        koha_client, dspace_client = _make_clients()
        task_id = task_manager.start_task(
            process_integration_logic,
            biblionumber,
            koha_client=koha_client,
            dspace_client=dspace_client,
        )
        return jsonify({"status": "accepted", "task_id": task_id}), 202
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/kdv/api/status/<task_id>", methods=["GET"])
def get_task_status(task_id):
    info = task_manager.get_status(task_id)
    return jsonify(info) if info else (jsonify({"status": "not_found"}), 404)


@app.route("/kdv/api/integrate/<int:biblionumber>", methods=["PUT"])
def update_record(biblionumber):
    koha, dspace = _make_clients()
    try:
        raw_xml = koha._get_biblio_xml(biblionumber)
        md = parse_marc_details(raw_xml)
        md["koha.biblionumber"] = str(biblionumber)

        meta = koha.get_biblio_metadata(biblionumber)
        item_uuid = meta.get("dspace_uuid") if meta else None

        if not item_uuid and md.get("handle"):
            item_uuid = dspace.find_item_uuid_by_handle(md["handle"])

        if not item_uuid:
            existing = dspace.find_item_by_biblionumber(biblionumber)
            if existing:
                item_uuid = existing["uuid"]

        if not item_uuid:
            return jsonify({"status": "error", "message": "Item not found"}), 404

        success = dspace.update_metadata(item_uuid, md)
        return (
            jsonify({"status": "success"})
            if success
            else (jsonify({"status": "error"}), 500)
        )

    except Exception as e:
        logger.error(f"UPDATE ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
