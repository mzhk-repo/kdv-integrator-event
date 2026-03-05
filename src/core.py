import os
import logging
import re
import time
import concurrent.futures
from io import BytesIO
from pymarc import parse_xml_to_array

from .config import INTEGRATOR_MOUNT_PATH, DSPACE_UI_URL
from .koha import KohaClient
from .dspace import DSpaceClient
from .services.covers import CoverService
from .services.files import FileService
from .mapping import METADATA_RULES, TYPE_CONVERSION

logger = logging.getLogger("KDV-Core")


def parse_marc_details(xml_data):
    try:
        reader = parse_xml_to_array(BytesIO(xml_data.encode("utf-8")))
        record = reader[0]
        extracted_data = {}
        for dspace_field, rule in METADATA_RULES.items():
            values = []
            sources = rule.get(
                "sources", [{"tag": rule.get("tag"), "subfield": rule.get("subfield")}]
            )
            for src in sources:
                tag = src.get("tag")
                sub = src.get("subfield")
                if not tag or tag not in record:
                    continue
                if rule.get("multivalue"):
                    for field in record.get_fields(tag):
                        val = field[sub] if sub in field else None
                        if val:
                            values.append(val)
                else:
                    val = record[tag][sub] if sub in record[tag] else None
                    if val:
                        values.append(val)
                        break
            final_values = []
            for v in values:
                if "regex" in rule:
                    match = re.search(rule["regex"], v)
                    if match:
                        v = match.group(1)
                    else:
                        continue
                if "conversion" in rule and rule["conversion"] == "type":
                    v = TYPE_CONVERSION.get(v, TYPE_CONVERSION.get("DEFAULT"))
                final_values.append(v)
            if final_values:
                extracted_data[dspace_field] = (
                    final_values if rule.get("multivalue") else final_values[0]
                )
        handle = None
        if "856" in record and "u" in record["856"]:
            full_url = record["856"]["u"]
            match = re.search(r"handle/(\d+/\d+)", full_url)
            if match:
                handle = match.group(1)
        extracted_data["handle"] = handle
        return extracted_data
    except Exception as e:
        logger.warning(f"Could not parse MARC details: {e}")
        return {}


def run_dspace_workflow(
    biblionumber, file_path, meta, koha_client=None, dspace_client=None
):
    """Execute metadata extraction and file upload to DSpace.

    Dependencies can be injected for testing.
    """
    local_koha = koha_client or KohaClient()
    local_dspace = dspace_client or DSpaceClient()

    logger.info(f"🚀 [DSpace-Thread] Starting metadata & upload for #{biblionumber}")

    raw_xml = local_koha._get_biblio_xml(biblionumber)
    md = parse_marc_details(raw_xml)
    md["koha.biblionumber"] = str(biblionumber)

    collection_uuid = meta.get("collection_uuid")
    if not collection_uuid:
        raise Exception("Collection UUID missing")

    existing_item = local_dspace.find_item_by_biblionumber(biblionumber)
    if existing_item:
        logger.warning(
            f"🔄 Item already exists (UUID: {existing_item['uuid']}). Linking only."
        )
        item_uuid = existing_item["uuid"]
        handle = existing_item.get("handle")
        final_link = (
            f"{DSPACE_UI_URL}/handle/{handle}"
            if handle
            else f"{DSPACE_UI_URL}/items/{item_uuid}"
        )
        return {"handle": final_link, "uuid": item_uuid, "status": "linked_existing"}

    item_data = local_dspace.create_item_direct(collection_uuid, md)
    if not item_data:
        raise Exception("Failed to create item in DSpace")

    item_uuid = item_data["uuid"]
    handle = item_data.get("handle")
    final_link = (
        f"{DSPACE_UI_URL}/handle/{handle}"
        if handle
        else f"{DSPACE_UI_URL}/items/{item_uuid}"
    )

    logger.info(f"📤 [DSpace-Thread] Uploading file to Item {item_uuid}")
    if not local_dspace.upload_to_item(item_uuid, file_path):
        raise Exception("Failed to upload file")

    logger.info(f"✅ [DSpace-Thread] Finished for #{biblionumber}")
    return {"handle": final_link, "uuid": item_uuid}


def process_integration_logic(
    task_id, biblionumber, koha_client=None, dspace_client=None
):
    """Main orchestration logic executed inside a background thread.

    Clients can be injected for testing or alternative implementations.
    """
    logger.info(f"⚙️ [Core] Processing Biblio #{biblionumber}")
    koha = koha_client or KohaClient()
    cover_service = CoverService(koha_client=koha)
    current_active_path = None

    LIMIT_WARNING = 150 * 1024 * 1024
    LIMIT_ERROR = 250 * 1024 * 1024

    try:
        # --- 1. SERIAL PHASE: Checks & Rename ---
        meta = koha.get_biblio_metadata(biblionumber)
        if not meta:
            raise Exception("No 956 field found")

        file_rel_path = meta["file_path"]
        original_full_path = os.path.join(INTEGRATOR_MOUNT_PATH, file_rel_path)

        if not os.path.exists(original_full_path):
            koha.set_status(biblionumber, "error", f"File missing: {file_rel_path}")
            raise Exception("File not found on disk")

        file_size = os.path.getsize(original_full_path)
        if file_size > LIMIT_ERROR:
            msg = f"FILE TOO LARGE ({round(file_size / 1024 / 1024)} MB)"
            koha.set_status(biblionumber, "error", msg)
            raise Exception(msg)
        if file_size > LIMIT_WARNING:
            koha.set_status(
                biblionumber, None, f"Warning: {round(file_size / 1024 / 1024)} MB"
            )

        # create file service for rename/versioning
        file_service = FileService()
        versioned_path = file_service.version_and_move(original_full_path, biblionumber)
        current_active_path = versioned_path

        # --- ⚡ 2. PARALLEL PHASE: DSpace + Cover ---
        dspace_result = None
        cover_url = None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # Task A: Cover
            pdf_dir = os.path.dirname(current_active_path)
            future_cover = executor.submit(
                cover_service.process_book,
                str(biblionumber),
                current_active_path,
                pdf_dir,
            )

            # Task B: DSpace
            future_dspace = executor.submit(
                run_dspace_workflow,
                biblionumber,
                current_active_path,
                meta,
                koha_client=koha,
                dspace_client=dspace_client,
            )

            logger.info("⚡ [Core] Parallel tasks started: Cover + DSpace")

            # Check Critical Task (DSpace)
            try:
                dspace_result = future_dspace.result()
            except Exception as e:
                logger.error(f"❌ [Core] DSpace Thread failed: {e}")
                raise e

            # Check Bonus Task (Cover)
            try:
                cover_res = future_cover.result(timeout=10)
                logger.info(f"🖼️ [Core] Cover result: {cover_res}")
                if cover_res.get("status") in ["success", "skipped"]:
                    for attempt in range(3):
                        real_url = koha.get_cover_image_url(biblionumber)
                        if real_url:
                            logger.info(f"🔗 [Core] Resolved Cover URL: {real_url}")
                            cover_url = real_url
                            break
                        else:
                            logger.info(
                                f"⏳ [Core] Waiting for cover API index (attempt {attempt + 1}/3)..."
                            )
                            time.sleep(1)

            except concurrent.futures.TimeoutError:
                logger.warning("⚠️ [Core] Cover generation timeout.")
            except Exception as e:
                logger.warning(f"⚠️ [Core] Cover Thread warning: {e}")

        # --- 3. FINALIZE ---
        if dspace_result:
            koha.set_success(
                biblionumber,
                dspace_result["handle"],
                item_uuid=dspace_result["uuid"],
                cover_url=cover_url,
            )

        return dspace_result

    except Exception as e:
        logger.error(f"❌ [Core] Logic Error processing #{biblionumber}: {e}")
        try:
            koha.set_status(biblionumber, "error", str(e))
        except Exception:
            pass

        if current_active_path and os.path.exists(current_active_path):
            # delegate error move to FileService
            file_service = FileService()
            file_service.move_to_error(current_active_path)
        raise e
