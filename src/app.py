import logging
from flask import Flask, jsonify, request, abort

from .tasks import task_manager
from .config import setup_logging, KDV_API_TOKEN
from .core import process_integration_logic, parse_marc_details

# wrappers imported here for DI in web handlers
from .clients.koha import KohaClientWrapper
from .clients.dspace import DSpaceClientWrapper

setup_logging()
logger = logging.getLogger("KDV-Core")

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-KDV-TOKEN, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

@app.before_request
def check_security():
    if request.path.endswith('/health') or request.method == 'OPTIONS':
        return
    if request.headers.get('X-KDV-TOKEN') != KDV_API_TOKEN:
        abort(401, description="Invalid Token")

@app.route('/kdv/api/health', methods=['GET'])
def healthcheck():
    return jsonify({"status": "ok", "mode": "v6.5-parallel-covers"})

def _make_clients():
    """Return a fresh pair of Koha/DSpace clients (wrappers) for glue code.

    In tests we can monkeypatch this function to return stubs.
    """
    return KohaClientWrapper(), DSpaceClientWrapper()


@app.route('/kdv/api/integrate/<int:biblionumber>', methods=['POST'])
def archive_record_async(biblionumber):
    try:
        koha_client, dspace_client = _make_clients()
        task_id = task_manager.start_task(
            process_integration_logic,
            biblionumber,
            koha_client=koha_client,
            dspace_client=dspace_client
        )
        return jsonify({"status": "accepted", "task_id": task_id}), 202
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/kdv/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    info = task_manager.get_status(task_id)
    return jsonify(info) if info else (jsonify({"status": "not_found"}), 404)

@app.route('/kdv/api/integrate/<int:biblionumber>', methods=['PUT'])
def update_record(biblionumber):
    koha, dspace = _make_clients()
    try:
        raw_xml = koha._get_biblio_xml(biblionumber)
        md = parse_marc_details(raw_xml)
        md['koha.biblionumber'] = str(biblionumber)
        
        meta = koha.get_biblio_metadata(biblionumber)
        item_uuid = meta.get('dspace_uuid') if meta else None

        if not item_uuid and md.get('handle'):
            item_uuid = dspace.find_item_uuid_by_handle(md['handle'])

        if not item_uuid:
            existing = dspace.find_item_by_biblionumber(biblionumber)
            if existing:
                item_uuid = existing['uuid']

        if not item_uuid:
            return jsonify({"status": "error", "message": "Item not found"}), 404

        success = dspace.update_metadata(item_uuid, md)
        return jsonify({"status": "success"}) if success else (jsonify({"status": "error"}), 500)

    except Exception as e:
        logger.error(f"UPDATE ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
