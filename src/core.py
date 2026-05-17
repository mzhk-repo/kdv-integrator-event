import contextlib
import os
import logging
import re
import shutil
import time
import uuid
import concurrent.futures
from io import BytesIO
from pymarc import parse_xml_to_array

from .config import INTEGRATOR_MOUNT_PATH, DSPACE_UI_URL
from .koha import KohaClient
from .dspace import DSpaceClient
from .services.covers import CoverService
from .services.files import FileService
from .services.pdf import (
    PDFOptimizerClient,
    has_optimizer_disk_space,
    needs_optimization,
)
from .mapping import METADATA_RULES, TYPE_CONVERSION

logger = logging.getLogger("KDV-Core")


def _optimizer_data_dir() -> str:
    return os.environ.get("OPTIMIZER_DATA_DIR") or os.environ.get(
        "DATA_DIR", "/data/kdv_optimize"
    )


def _optimizer_input_dir() -> str:
    return os.environ.get("OPTIMIZER_INPUT_DIR") or os.environ.get(
        "INPUT_DIR", os.path.join(_optimizer_data_dir(), "input")
    )


def _optimizer_output_dir() -> str:
    return os.environ.get("OPTIMIZER_OUTPUT_DIR") or os.environ.get(
        "OUTPUT_DIR", os.path.join(_optimizer_data_dir(), "output")
    )


def _file_mb(path: str) -> float | None:
    try:
        return round(os.path.getsize(path) / 1024 / 1024, 2)
    except OSError:
        return None


def _disk_free_mb(path: str) -> float | None:
    try:
        return round(shutil.disk_usage(path).free / 1024 / 1024, 2)
    except OSError:
        return None


def _build_pdf_telemetry(pdf_path: str) -> dict:
    return {
        "pdf_optimized": "false",
        "pdf_fallback_reason": None,
        "pdf_original_mb": _file_mb(pdf_path),
        "pdf_final_mb": _file_mb(pdf_path),
        "pdf_pages": None,
        "pdf_optimization_time_ms": None,
        "pdf_thread_wait_ms": None,
        "pdf_disk_free_mb": _disk_free_mb(_optimizer_data_dir()),
    }


def _make_optimizer_client() -> PDFOptimizerClient | None:
    base_url = os.environ.get("OPTIMIZER_URL", "").strip()
    if not base_url:
        return None
    timeout = int(os.environ.get("OPTIMIZER_TIMEOUT", "130"))
    return PDFOptimizerClient(base_url=base_url, timeout=timeout)


def _prepare_pdf_for_upload(
    pdf_path: str,
    skip_optimization: bool,
    optimizer_client: PDFOptimizerClient | None = None,
) -> tuple[str, dict, tuple[str, str]]:
    telemetry = _build_pdf_telemetry(pdf_path)
    job_id = str(uuid.uuid4())
    input_tmp = os.path.join(_optimizer_input_dir(), f"{job_id}.pdf")
    output_tmp = os.path.join(_optimizer_output_dir(), f"{job_id}.pdf")
    cleanup_paths = (input_tmp, output_tmp)

    if skip_optimization:
        telemetry["pdf_optimized"] = "skipped_by_user"
        logger.info(
            "PDF optimization skipped by user: source=%s original_mb=%s",
            pdf_path,
            telemetry["pdf_original_mb"],
        )
        return pdf_path, telemetry, cleanup_paths

    if not needs_optimization(pdf_path, skip=False):
        telemetry["pdf_optimized"] = "skipped_by_size"
        logger.info(
            "PDF optimization skipped by heuristic: source=%s original_mb=%s",
            pdf_path,
            telemetry["pdf_original_mb"],
        )
        return pdf_path, telemetry, cleanup_paths

    if not has_optimizer_disk_space(pdf_path, data_dir=_optimizer_data_dir()):
        telemetry["pdf_optimized"] = "skipped_no_disk"
        telemetry["pdf_disk_free_mb"] = _disk_free_mb(_optimizer_data_dir())
        logger.warning(
            "PDF optimization skipped: not enough shared-volume disk space "
            "source=%s original_mb=%s disk_free_mb=%s data_dir=%s",
            pdf_path,
            telemetry["pdf_original_mb"],
            telemetry["pdf_disk_free_mb"],
            _optimizer_data_dir(),
        )
        return pdf_path, telemetry, cleanup_paths

    client = optimizer_client or _make_optimizer_client()
    if client is None:
        telemetry["pdf_fallback_reason"] = "optimizer_unavailable"
        logger.warning(
            "PDF optimization fallback: optimizer client unavailable "
            "source=%s original_mb=%s job_id=%s",
            pdf_path,
            telemetry["pdf_original_mb"],
            job_id,
        )
        return pdf_path, telemetry, cleanup_paths

    try:
        os.makedirs(os.path.dirname(input_tmp), exist_ok=True)
        os.makedirs(os.path.dirname(output_tmp), exist_ok=True)
        shutil.copy2(pdf_path, input_tmp)
        logger.info(
            "PDF optimization job submitted to optimizer: job_id=%s source=%s "
            "input_tmp=%s expected_output=%s original_mb=%s",
            job_id,
            pdf_path,
            input_tmp,
            output_tmp,
            telemetry["pdf_original_mb"],
        )
        result = client.optimize(pdf_path, job_id)
        final_pdf_path = result.path
        telemetry.update(
            {
                "pdf_optimized": "true" if result.success else "false",
                "pdf_fallback_reason": result.fallback_reason,
                "pdf_original_mb": result.original_mb or telemetry["pdf_original_mb"],
                "pdf_final_mb": _file_mb(final_pdf_path),
                "pdf_optimization_time_ms": result.optimization_time_ms,
                "pdf_thread_wait_ms": result.thread_wait_ms,
            }
        )
        if result.success:
            logger.info(
                "PDF optimization completed: job_id=%s final_path=%s original_mb=%s "
                "final_mb=%s optimization_time_ms=%s thread_wait_ms=%s",
                job_id,
                final_pdf_path,
                telemetry["pdf_original_mb"],
                telemetry["pdf_final_mb"],
                telemetry["pdf_optimization_time_ms"],
                telemetry["pdf_thread_wait_ms"],
            )
        else:
            logger.warning(
                "PDF optimization fallback: job_id=%s reason=%s source=%s "
                "upload_path=%s original_mb=%s final_mb=%s",
                job_id,
                telemetry["pdf_fallback_reason"],
                pdf_path,
                final_pdf_path,
                telemetry["pdf_original_mb"],
                telemetry["pdf_final_mb"],
            )
        return final_pdf_path, telemetry, cleanup_paths
    except Exception as exc:
        logger.warning(
            "PDF optimization exception, uploading original PDF: job_id=%s "
            "source=%s error=%s",
            job_id,
            pdf_path,
            exc,
        )
        telemetry["pdf_fallback_reason"] = "exception"
        telemetry["pdf_final_mb"] = _file_mb(pdf_path)
        return pdf_path, telemetry, cleanup_paths


def _cleanup_optimizer_files(paths: tuple[str, str]) -> None:
    for file_path in paths:
        with contextlib.suppress(FileNotFoundError):
            os.remove(file_path)


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
    biblionumber,
    file_path,
    meta,
    koha_client=None,
    dspace_client=None,
    skip_optimization: bool = False,
    optimizer_client: PDFOptimizerClient | None = None,
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

    final_pdf_path = file_path
    pdf_telemetry = _build_pdf_telemetry(file_path)
    cleanup_paths = ()
    try:
        final_pdf_path, pdf_telemetry, cleanup_paths = _prepare_pdf_for_upload(
            file_path,
            skip_optimization=skip_optimization,
            optimizer_client=optimizer_client,
        )
        logger.info(f"📤 [DSpace-Thread] Uploading file to Item {item_uuid}")
        if not local_dspace.upload_to_item(item_uuid, final_pdf_path):
            raise Exception("Failed to upload file")
    finally:
        _cleanup_optimizer_files(cleanup_paths)

    logger.info(f"✅ [DSpace-Thread] Finished for #{biblionumber}")
    result = {"handle": final_link, "uuid": item_uuid}
    result.update(pdf_telemetry)
    return result


def process_integration_logic(
    task_id,
    biblionumber,
    koha_client=None,
    dspace_client=None,
    skip_optimization: bool = False,
    optimizer_client: PDFOptimizerClient | None = None,
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
                skip_optimization=skip_optimization,
                optimizer_client=optimizer_client,
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
