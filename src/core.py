import contextlib
import fcntl
import hashlib
import json
import os
import logging
import re
import shutil
import time
import uuid
import concurrent.futures
from io import BytesIO
from pathlib import Path
import tempfile
from pymarc import parse_xml_to_array

from .config import INTEGRATOR_MOUNT_PATH, DSPACE_UI_URL
from .koha import KohaClient
from .dspace import DSpaceClient, DSpaceRestError
from .services.covers import CoverService
from .services.cover_pipeline import verify_drive_download as _verify_drive_download
from .services.cover_pipeline import (
    InvalidPDFCoverError, download_and_normalize, publish_cover, render_pdf_cover,
)
from .services.files import FileService
from .services.sources import (
    SourceResolutionError,
    SourceResolver,
)
from .services.pdf import (
    PDFOptimizerClient,
    has_optimizer_disk_space,
    needs_optimization,
)
from .mapping import METADATA_RULES, TYPE_CONVERSION, strip_metadata_edges
from .cover_state.drive import DriveCheckResult, DriveMetadataError, check_drive_metadata
from .cover_state.state_machine import StateMachine

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


def _primary_download_url(bitstream_data) -> str | None:
    if not isinstance(bitstream_data, dict):
        return None
    bitstream_uuid = bitstream_data.get("uuid")
    if not bitstream_uuid:
        return None
    return f"{DSPACE_UI_URL}/bitstreams/{bitstream_uuid}/download"


def _repair_missing_dspace_handle_link(koha, dspace, biblionumber, meta, record_state):
    if not meta.get("file_path") or not record_state or record_state.get("status") != "ok":
        return False
    item_uuid = record_state.get("dspace_item_uuid") or meta.get("dspace_uuid")
    if not item_uuid:
        return False
    links = meta.get("dspace_links") or []
    has_record_link = any(
        "/handle/" in link or f"/items/{item_uuid}" in link for link in links
    )
    has_file_link = any("/bitstreams/" in link for link in links)
    if has_record_link and has_file_link:
        return False

    dspace = dspace or DSpaceClient()
    try:
        item = dspace.get_item(item_uuid)
    except DSpaceRestError as error:
        if error.status_code == 404:
            logger.info("Stored DSpace Item is missing; record_uid=%r item_uuid=%s", meta.get("record_uid"), item_uuid)
            return "missing"
        raise
    handle = item.get("handle")
    handle_url = f"{DSPACE_UI_URL}/handle/{handle}" if handle else f"{DSPACE_UI_URL}/items/{item_uuid}"
    bitstream = dspace.get_primary_bitstream(item_uuid)
    primary_download_url = _primary_download_url(bitstream)
    if not primary_download_url:
        raise RuntimeError("DSpace primary bitstream is unavailable for link repair")
    if koha.repair_dspace_links(biblionumber, primary_download_url, handle_url) is not True:
        raise RuntimeError("Koha DSpace links repair was not confirmed")
    readback = koha.get_biblio_metadata(biblionumber)
    if not readback or any(
        link not in readback.get("dspace_links", [])
        for link in (primary_download_url, handle_url)
    ):
        raise RuntimeError("Koha DSpace links read-back was not confirmed")
    logger.info("Restored DSpace links for record_uid=%r item_uuid=%s", meta.get("record_uid"), item_uuid)
    return True


def _disk_free_mb(path: str) -> float | None:
    try:
        return round(shutil.disk_usage(path).free / 1024 / 1024, 2)
    except OSError:
        return None


def restore_missing_957_from_state(koha, biblionumber, meta, record_state):
    if not meta or not record_state:
        return meta
    values = {
        "item_uuid": record_state.get("dspace_item_uuid"),
        "cover_asset_sha256": record_state.get("cover_asset_sha256"),
    }
    missing = {
        key: value for key, value in values.items()
        if value and not meta.get("dspace_uuid" if key == "item_uuid" else key)
    }
    if missing and koha.restore_missing_957_metadata(
        biblionumber, record_uid=meta.get("record_uid"), **missing
    ) is not True:
        raise RuntimeError(f"Koha 957 state restoration failed for #{biblionumber}")
    if "item_uuid" in missing:
        meta["dspace_uuid"] = missing["item_uuid"]
    if "cover_asset_sha256" in missing:
        meta["cover_asset_sha256"] = missing["cover_asset_sha256"]
    return meta


def _resolve_cover_url(koha, biblionumber, cover_res, update_koha: bool = False):
    if not isinstance(cover_res, dict):
        return None
    if cover_res.get("status") not in ["success", "skipped"]:
        return None

    for attempt in range(3):
        real_url = koha.get_cover_image_url(biblionumber)
        if real_url:
            logger.info(f"🔗 [Core] Resolved Cover URL: {real_url}")
            if update_koha:
                try:
                    koha.set_cover_url(biblionumber, real_url)
                except Exception as e:
                    logger.warning(f"⚠️ [Core] Failed to update 956$c: {e}")
            return real_url

        logger.info(
            f"⏳ [Core] Waiting for cover API index (attempt {attempt + 1}/3)..."
        )
        time.sleep(1)

    return None


def _build_pdf_telemetry(pdf_path: str, requested_dpi: int | None = None) -> dict:
    return {
        "pdf_optimized": "false",
        "pdf_fallback_reason": None,
        "pdf_original_mb": _file_mb(pdf_path),
        "pdf_final_mb": _file_mb(pdf_path),
        "pdf_pages": None,
        "pdf_optimization_time_ms": None,
        "pdf_thread_wait_ms": None,
        "pdf_disk_free_mb": _disk_free_mb(_optimizer_data_dir()),
        "pdf_requested_dpi": requested_dpi,
        "pdf_applied_dpi": None,
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
    dpi: int | None = None,
) -> tuple[str, dict, tuple[str, str]]:
    telemetry = _build_pdf_telemetry(pdf_path, requested_dpi=dpi)
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
        result = (
            client.optimize(pdf_path, job_id, dpi=dpi)
            if dpi is not None
            else client.optimize(pdf_path, job_id)
        )
        final_pdf_path = result.path
        telemetry.update(
            {
                "pdf_optimized": "true" if result.success else "false",
                "pdf_fallback_reason": result.fallback_reason,
                "pdf_original_mb": result.original_mb or telemetry["pdf_original_mb"],
                "pdf_final_mb": _file_mb(final_pdf_path),
                "pdf_optimization_time_ms": result.optimization_time_ms,
                "pdf_thread_wait_ms": result.thread_wait_ms,
                "pdf_applied_dpi": result.applied_dpi if result.success else None,
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


def _parse_additional_file_paths(raw_paths: str | None) -> list[str]:
    if not raw_paths:
        return []
    return [part.strip() for part in raw_paths.split("|") if part.strip()]


def _source_resolver() -> SourceResolver:
    return SourceResolver(INTEGRATOR_MOUNT_PATH)


def _looks_like_gdrive_url(raw_path: str | None) -> bool:
    return bool(raw_path and "drive.google.com" in raw_path)


def _upload_additional_files(dspace_client, item_uuid, raw_paths: str | None) -> dict:
    uploaded = []
    failed = []

    resolver = _source_resolver()

    for relative_path in _parse_additional_file_paths(raw_paths):
        try:
            resolved_source = resolver.resolve_additional(relative_path)
            resolved_source = resolver.materialize(resolved_source)
        except SourceResolutionError as exc:
            if _looks_like_gdrive_url(relative_path):
                logger.warning(
                    "Additional Google Drive file download failed: item_uuid=%s path=%s error=%s",
                    item_uuid,
                    relative_path,
                    exc,
                )
                failed.append({"path": relative_path, "reason": str(exc)})
                continue
            logger.warning(
                "Additional DSpace file path rejected: item_uuid=%s relative_path=%s error=%s",
                item_uuid,
                relative_path,
                exc,
            )
            failed.append({"path": relative_path, "reason": "invalid_path"})
            continue
        except Exception as exc:
            if _looks_like_gdrive_url(relative_path):
                logger.warning(
                    "Additional Google Drive file download failed: item_uuid=%s path=%s error=%s",
                    item_uuid,
                    relative_path,
                    exc,
                )
                failed.append({"path": relative_path, "reason": str(exc)})
                continue
            raise

        full_path = resolved_source.local_path if resolved_source else None
        if not full_path or not os.path.exists(full_path):
            logger.warning(
                "Additional DSpace file missing: item_uuid=%s relative_path=%s full_path=%s",
                item_uuid,
                relative_path,
                full_path,
            )
            failed.append({"path": relative_path, "reason": "missing"})
            continue

        upload_name = (
            resolved_source.original_name
            if resolved_source.source_type == "gdrive"
            else None
        )
        try:
            if dspace_client.upload_to_item(item_uuid, full_path, upload_name=upload_name):
                logger.info(
                    "Additional DSpace file uploaded: item_uuid=%s relative_path=%s full_path=%s source_type=%s lifecycle_policy=%s",
                    item_uuid,
                    relative_path,
                    full_path,
                    resolved_source.source_type,
                    resolved_source.lifecycle_policy,
                )
                uploaded.append(relative_path)
            else:
                logger.warning(
                    "Additional DSpace file upload returned False: item_uuid=%s relative_path=%s",
                    item_uuid,
                    relative_path,
                )
                failed.append({"path": relative_path, "reason": "upload_failed"})
        except Exception as exc:
            logger.warning(
                "Additional DSpace file upload failed: item_uuid=%s relative_path=%s error=%s",
                item_uuid,
                relative_path,
                exc,
            )
            failed.append({"path": relative_path, "reason": str(exc)})

    return {
        "additional_files_uploaded": uploaded,
        "additional_files_failed": failed,
    }


def _resolve_mount_relative_path(
    relative_path: str | None, field_name: str
) -> str | None:
    return _source_resolver().resolve_local_path(relative_path, field_name)


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
                v = strip_metadata_edges(v)
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
    upload_name: str | None = None,
    dpi: int | None = None,
    replace_existing: bool = False,
    result_callback=None,
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
    record_uid = meta.get("record_uid")
    if record_uid:
        try:
            parsed_uid = uuid.UUID(record_uid)
            if parsed_uid.version != 7:
                raise ValueError
            record_uid = str(parsed_uid)
        except (ValueError, AttributeError, TypeError):
            raise ValueError("DSpace workflow requires MARC 001 UUIDv7") from None
        md["koha.uid"] = record_uid

    collection_uuid = meta.get("collection_uuid")
    if not collection_uuid:
        raise Exception("Collection UUID missing")

    find_by_uid = getattr(local_dspace, "find_item_by_record_uid", None)
    existing_item = find_by_uid(record_uid) if record_uid and find_by_uid else None
    replacement_old_bitstream_uuids = []
    primary_bitstream = None
    if existing_item:
        logger.info("DSpace item found for biblionumber=%s uuid=%s", biblionumber, existing_item["uuid"])
        item_uuid = existing_item["uuid"]
        handle = existing_item.get("handle")
        final_link = (
            f"{DSPACE_UI_URL}/handle/{handle}"
            if handle
            else f"{DSPACE_UI_URL}/items/{item_uuid}"
        )
        primary_bitstream = local_dspace.get_primary_bitstream(item_uuid)
        logger.info(
            "DSpace replacement check item_uuid=%s enabled=%s primary_bitstream_uuid=%s",
            item_uuid, replace_existing,
            primary_bitstream.get("uuid") if primary_bitstream else None,
        )
        if primary_bitstream and replace_existing:
            if not primary_bitstream.get("uuid"):
                raise RuntimeError("Existing DSpace primary bitstream has no UUID")
        elif primary_bitstream:
            result = {
                "handle": final_link,
                "uuid": item_uuid,
                "status": "linked_existing",
                "bitstream_uuid": primary_bitstream.get("uuid"),
                "primary_download_url": _primary_download_url(primary_bitstream),
            }
            result.update(
                _upload_additional_files(local_dspace, item_uuid, meta.get("additional_files"))
            )
            return result
        # A previous attempt may have created the Item and failed before its first upload.
        if record_uid:
            local_dspace.update_metadata(item_uuid, md)
    else:
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
    pdf_telemetry = _build_pdf_telemetry(file_path, requested_dpi=dpi)
    cleanup_paths = ()
    try:
        final_pdf_path, pdf_telemetry, cleanup_paths = _prepare_pdf_for_upload(
            file_path,
            skip_optimization=skip_optimization,
            optimizer_client=optimizer_client,
            dpi=dpi,
        )
        primary_upload_name = upload_name or os.path.basename(file_path)
        if replace_existing:
            old_bitstreams = local_dspace.get_original_bitstreams(item_uuid)
            previous_uuid = meta.get('previous_dspace_bitstream_uuid')
            if previous_uuid and (
                meta.get('previous_dspace_item_uuid') != item_uuid
                or previous_uuid not in {bitstream.get('uuid') for bitstream in old_bitstreams}
            ):
                logger.warning(
                    'Ignoring stale DSpace bitstream identity: saved_item_uuid=%s '
                    'target_item_uuid=%s saved_bitstream_uuid=%s',
                    meta.get('previous_dspace_item_uuid'), item_uuid, previous_uuid,
                )
                previous_uuid = None
            replacement_old_bitstream_uuids = list(dict.fromkeys(
                [
                    bitstream.get("uuid") for bitstream in old_bitstreams
                    if bitstream.get("uuid")
                    and (
                        (primary_bitstream and bitstream["uuid"] == primary_bitstream.get("uuid"))
                        or bitstream["uuid"] == previous_uuid
                        or bitstream.get("name") == primary_upload_name
                    )
                ]
            ))
            logger.info(
                "DSpace replacement candidates item_uuid=%s filename=%s old_bitstream_uuids=%s",
                item_uuid, primary_upload_name, replacement_old_bitstream_uuids,
            )
        logger.info(
            "📤 [DSpace-Thread] Uploading file to Item %s upload_path=%s upload_name=%s",
            item_uuid,
            final_pdf_path,
            primary_upload_name,
        )
        primary_bitstream = local_dspace.upload_to_item(
            item_uuid, final_pdf_path, upload_name=primary_upload_name
        )
        if not primary_bitstream:
            raise Exception("Failed to upload file")
        if replace_existing:
            if not primary_bitstream.get("uuid"):
                raise RuntimeError("DSpace did not return the replacement bitstream UUID")
            local_dspace.verify_bitstream_upload(primary_bitstream["uuid"], final_pdf_path)
            local_dspace.set_primary_bitstream(item_uuid, primary_bitstream["uuid"])
            confirmed_primary = local_dspace.get_primary_bitstream(item_uuid)
            if not confirmed_primary or confirmed_primary.get("uuid") != primary_bitstream["uuid"]:
                raise RuntimeError("DSpace primary bitstream replacement was not confirmed")
        primary_download_url = _primary_download_url(primary_bitstream)
        additional_telemetry = _upload_additional_files(
            local_dspace, item_uuid, meta.get("additional_files")
        )
    finally:
        _cleanup_optimizer_files(cleanup_paths)

    logger.info(f"✅ [DSpace-Thread] Finished for #{biblionumber}")
    result = {
        "handle": final_link,
        "uuid": item_uuid,
        "bitstream_uuid": primary_bitstream.get("uuid"),
        "primary_download_url": primary_download_url,
    }
    if replace_existing:
        result.update({
            "status": "replaced",
            "old_bitstream_uuids": replacement_old_bitstream_uuids,
        })
        if replacement_old_bitstream_uuids:
            result["old_bitstream_uuid"] = replacement_old_bitstream_uuids[0]
    result.update(pdf_telemetry)
    result.update(additional_telemetry)
    if result_callback:
        result_callback(result)
    return result


def process_integration_logic(
    task_id, biblionumber, koha_client=None, dspace_client=None,
    skip_optimization: bool = False, optimizer_client: PDFOptimizerClient | None = None,
    dpi: int | None = None, state_machine: StateMachine | None = None,
    force_file_refresh: bool = False,
):
    """Apply the durable Drive gate before the existing asynchronous workflow."""
    options = dict(koha_client=koha_client, dspace_client=dspace_client,
                   skip_optimization=skip_optimization, optimizer_client=optimizer_client, dpi=dpi)
    if force_file_refresh and not os.environ.get("COVER_STATE_DB_PATH") and state_machine is None:
        raise RuntimeError("COVER_STATE_DB_PATH is required for UI bitstream replacement")
    if state_machine is None and not os.environ.get("COVER_STATE_DB_PATH"):
        return _run_integration_logic(task_id, biblionumber, **options)
    state = state_machine or StateMachine()
    # ponytail: one writer across API/Robot processes; use per-record locks if throughput requires it.
    with open(os.path.join(os.path.dirname(state.db_path), '.workflow.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        koha = koha_client or KohaClient()
        options['koha_client'] = koha
        meta = koha.get_biblio_metadata(biblionumber)
        if force_file_refresh and (not meta or not meta.get('file_path')):
            raise ValueError('UI bitstream replacement requires a 956$u Drive PDF source')
        resolver = _source_resolver()
        refs = {
            'file': resolver.gdrive_parser.parse(meta.get('file_path'), '956$u') if meta else None,
            'cover': resolver.gdrive_parser.parse(meta.get('cover_path'), '956$p') if meta else None,
        }
        if force_file_refresh and refs['file'] is None:
            raise ValueError('UI bitstream replacement requires a 956$u Google Drive PDF')
        if not any(refs.values()):
            return _run_integration_logic(task_id, biblionumber, meta=meta, resolver=resolver, **options)
        uid = meta.get('record_uid')
        try:
            if not uid or uuid.UUID(uid).version != 7:
                raise ValueError('Drive workflow requires MARC 001 UUIDv7')
            uid = str(uuid.UUID(uid))
        except (ValueError, AttributeError):
            raise ValueError('Drive workflow requires MARC 001 UUIDv7') from None
        checks = {}
        downstream_started = False
        try:
            existing = state.get(uid)
            restore_missing_957_from_state(koha, biblionumber, meta, existing)
            if existing:
                meta = dict(
                    meta,
                    previous_dspace_item_uuid=existing['dspace_item_uuid'],
                    previous_dspace_bitstream_uuid=existing['dspace_bitstream_uuid'],
                )
            if existing is not None and existing['status'] != 'ok' and not state.is_retry_eligible(uid):
                return {'status': 'deferred', 'reason': 'retry_backoff_or_cutoff'}
            inputs_sha = hashlib.sha256(json.dumps({
                'file': refs['file'].file_id if refs['file'] else meta.get('file_path'),
                'cover': refs['cover'].file_id if refs['cover'] else meta.get('cover_path'),
                'additional': meta.get('additional_files'), 'collection': meta.get('collection_uuid'),
                'dpi': dpi, 'skip_optimization': skip_optimization,
            }, sort_keys=True).encode()).hexdigest()
            checkpoint = state.get_cover_work(uid)
            if checkpoint and checkpoint['inputs_sha256'] != inputs_sha:
                checkpoint = None
            for source, ref in refs.items():
                if ref is not None:
                    staged = checkpoint['sources'].get(source) if checkpoint else None
                    if staged and staged[0] == ref.file_id:
                        checks[source] = DriveCheckResult('resume', staged[1])
                    elif (existing and existing['status'] == 'ok'
                          and existing[f'{source}_source_id'] == ref.file_id
                          and not (force_file_refresh and source == 'file')):
                        # Another source's gate may already have marked the whole record pending.
                        checks[source] = DriveCheckResult('noop')
                    else:
                        checks[source] = check_drive_metadata(
                            state, uid, ref.file_id, source=source, resource_key=ref.resource_key,
                            drive_source=resolver.gdrive_source,
                            retry_checked=True,
                            force_refresh=force_file_refresh and source == 'file',
                        )
                    logger.info('Drive gate record_uid=%r source=%s action=%s', uid, source, checks[source].action)
                    if checks[source].action == 'deferred':
                        return {'status': 'deferred', 'reason': 'retry_backoff_or_cutoff'}
            file_work = bool(meta.get('file_path')) and (
                'file' not in checks or checks['file'].action not in ('noop', 'same_content')
            )
            # Additional/local sources are not covered by the Drive identity gate.
            file_work = file_work or bool(meta.get('additional_files'))
            cover_work = (
                checks['cover'].action not in ('noop', 'same_content') if 'cover' in checks
                else bool(meta.get('cover_path')) or (file_work and (
                    'file' not in checks or checks['file'].action not in ('noop', 'same_content')
                ))
            )
            if checkpoint:
                file_work = bool(checkpoint['file_work']) or force_file_refresh
                cover_work = True
            elif (refs['cover'] or (not meta.get('cover_path') and refs['file'])) and not (
                existing and existing['cover_asset_sha256']
            ):
                # Upgrade confirmed legacy covers to the canonical WebP asset.
                cover_work = True
            if not file_work and not cover_work:
                link_repair = _repair_missing_dspace_handle_link(
                    koha, options['dspace_client'], biblionumber, meta, existing
                )
                if link_repair is True:
                    return {'status': 'links_repaired'}
                if link_repair != 'missing':
                    return {'status': 'noop', 'reason': 'confirmed_sources_unchanged'}
                # A 404 proves the saved DSpace identity is stale. Reconcile by
                # koha.uid and recreate from the unchanged Drive source if absent.
                if refs['file'] is not None:
                    checks['file'] = check_drive_metadata(
                        state, uid, refs['file'].file_id, source='file',
                        resource_key=refs['file'].resource_key,
                        drive_source=resolver.gdrive_source, retry_checked=True,
                        force_refresh=True,
                    )
                    if checks['file'].action == 'deferred':
                        return {'status': 'deferred', 'reason': 'retry_backoff_or_cutoff'}
                    if checks['file'].action == 'same_content':
                        checks['file'] = DriveCheckResult(
                            'resource_changed', checks['file'].sha256, checks['file'].metadata
                        )
                file_work = True
                # Keep the old Item UUID to reject an unrelated legacy
                # biblionumber match; its bitstream cannot belong to a new Item.
                meta = dict(meta, previous_dspace_bitstream_uuid=None)
            if not state.mark_pending(uid):
                return {'status': 'deferred', 'reason': 'retry_cutoff'}
            downstream_started = True
            current = state.get(uid)
            sources = {
                source: (ref.file_id, checks[source].sha256 or current[f'{source}_source_sha256'])
                for source, ref in refs.items() if ref is not None
            }
            if (cover_work or file_work) and (refs['cover'] or (not meta.get('cover_path') and refs['file'])):
                if not cover_work and not checkpoint:
                    state.save_cover_work(
                        uid, inputs_sha, sources, existing['cover_asset_sha256'],
                        file_work=file_work,
                    )
                    checkpoint = state.get_cover_work(uid)
                result = _run_external_cover_cycle(
                    task_id, biblionumber, state, uid, inputs_sha, sources,
                    meta, resolver, checks, file_work, checkpoint, options,
                )
            else:
                result = _run_integration_logic(
                    task_id, biblionumber, meta=meta, resolver=resolver, checks=checks,
                    file_work=file_work, cover_work=cover_work, strict=True,
                    source_shas={source: value[1] for source, value in sources.items()}, **options,
                )
            state.complete_cycle(uid, sources, result)
            return result
        except Exception as error:
            # Metadata failures already incremented/saturated their own retry state.
            if downstream_started:
                state.record_result(uid, success=False, partial=True,
                                    permanent=isinstance(error, InvalidPDFCoverError))
            if isinstance(error, DriveMetadataError):
                koha.set_status(biblionumber, 'error', str(error))
            raise


def _run_external_cover_cycle(task_id, biblionumber, state, uid, inputs_sha,
                              sources, meta, resolver, checks, file_work, checkpoint, options):
    configured = os.environ.get('COVERS_STORAGE_PATH')
    if not configured:
        raise ValueError('COVERS_STORAGE_PATH is required for external cover write-back')
    if checkpoint:
        asset_sha = checkpoint['cover_asset_sha256']
        if re.fullmatch(r'[0-9a-f]{64}', asset_sha) is None:
            raise ValueError('Invalid checkpoint asset SHA')
        asset = Path(configured) / 'assets' / f'{asset_sha}.webp'
        # Validate the durable asset; publish_cover reuses it without rewriting.
        publish_cover(asset, expected_sha256=asset_sha)
    else:
        with tempfile.TemporaryDirectory(prefix='kdv-cover-normalize-') as temporary:
            output = Path(temporary) / 'cover.webp'
            if meta.get('cover_path'):
                normalized = download_and_normalize(
                    meta['cover_path'], output, resolver=resolver, metadata=checks['cover'].metadata,
                )
                if normalized['source_sha256'] != sources['cover'][1]:
                    raise RuntimeError('Cover source SHA changed after the Drive gate')
                asset_sha = normalized['cover_asset_sha256']
            else:
                primary = resolver.resolve_primary(meta['file_path'])
                primary = resolver.materialize(primary, metadata=checks['file'].metadata)
                _verify_drive_download(primary.local_path, sources['file'][1])
                asset_sha = render_pdf_cover(primary.local_path, output)
            publish_cover(output, expected_sha256=asset_sha)
        state.save_cover_work(uid, inputs_sha, sources, asset_sha, file_work=file_work)
    result = checkpoint['result'] if checkpoint else None
    if file_work and result is None:
        # Explicit Drive covers use WebP; the legacy PDF cover fallback is Phase 5.
        def checkpoint_dspace_result(dspace_result):
            state.save_cover_work(
                uid, inputs_sha, sources, asset_sha, file_work=True,
                result=dspace_result,
            )

        result = _run_integration_logic(
            task_id, biblionumber, meta=meta, resolver=resolver, checks=checks,
            file_work=True, cover_work=False, strict=True, write_back=False,
            source_shas={source: value[1] for source, value in sources.items()},
            result_callback=checkpoint_dspace_result, **options,
        )
        state.save_cover_work(uid, inputs_sha, sources, asset_sha, file_work=True, result=result)
    koha = options['koha_client']
    if file_work:
        confirmed = koha.set_success(
            biblionumber, result['handle'], item_uuid=result['uuid'], cover_url=asset_sha,
            primary_download_url=result.get('primary_download_url'),
        )
    else:
        confirmed = koha.set_cover_url(biblionumber, asset_sha)
    if confirmed is not True:
        raise RuntimeError('Koha cover write-back was not confirmed')
    readback = koha.get_biblio_metadata(biblionumber)
    if (not readback or readback.get('cover_asset_sha256') != asset_sha
            or str(readback.get('record_uid')).lower() != uid):
        raise RuntimeError('Koha 957$c read-back was not confirmed')
    if file_work and readback.get('dspace_uuid') != result['uuid']:
        raise RuntimeError('Koha 957$3 read-back was not confirmed')
    if file_work and any(link not in readback.get('dspace_links', []) for link in (
        result['handle'], result.get('primary_download_url'),
    ) if link):
        raise RuntimeError('Koha 856 read-back was not confirmed')
    old_bitstream_uuids = (
        result.get('old_bitstream_uuids') or
        ([result['old_bitstream_uuid']] if result.get('old_bitstream_uuid') else [])
    ) if file_work and result else []
    logger.info(
        'DSpace replacement cleanup item_uuid=%s old_bitstream_uuids=%s new_bitstream_uuid=%s',
        result.get('uuid') if file_work and result else None,
        old_bitstream_uuids,
        result.get('bitstream_uuid') if file_work and result else None,
    )
    for old_bitstream_uuid in old_bitstream_uuids:
        if old_bitstream_uuid == result.get('bitstream_uuid'):
            raise RuntimeError('DSpace replacement returned the existing bitstream UUID')
        dspace = options['dspace_client'] or DSpaceClient()
        dspace.delete_bitstream(old_bitstream_uuid)
    return dict(result or {'status': 'cover_updated'}, cover_asset_sha256=asset_sha)


def _run_integration_logic(
    task_id,
    biblionumber,
    koha_client=None,
    dspace_client=None,
    skip_optimization: bool = False,
    optimizer_client: PDFOptimizerClient | None = None,
    dpi: int | None = None,
    *, meta=None, resolver=None, checks=None, file_work=True, cover_work=True, strict=False,
    source_shas=None, write_back=True, result_callback=None,
):
    """Main orchestration logic executed inside a background thread.

    Clients can be injected for testing or alternative implementations.
    """
    logger.info(f"⚙️ [Core] Processing Biblio #{biblionumber}")
    koha = koha_client or KohaClient()
    cover_service = CoverService(koha_client=koha)
    current_active_path = None
    current_lifecycle_policy = None

    LIMIT_WARNING = 150 * 1024 * 1024
    LIMIT_ERROR = 250 * 1024 * 1024

    try:
        # --- 1. SERIAL PHASE: Checks & Rename ---
        meta = meta if meta is not None else koha.get_biblio_metadata(biblionumber)
        if not meta:
            raise Exception("No 956 field found")

        file_rel_path = meta["file_path"]
        cover_rel_path = meta.get("cover_path")
        source_resolver = resolver or _source_resolver()
        checks = checks or {}
        source_shas = source_shas or {}
        cover_source = source_resolver.resolve_cover(cover_rel_path) if cover_work else None
        if cover_source and cover_source.source_type == 'gdrive':
            cover_source = source_resolver.materialize(
                cover_source, metadata=checks.get('cover').metadata if checks.get('cover') else None,
                allowed_mime_types={'image/jpeg', 'image/png', 'image/webp'},
            )
            if strict:
                _verify_drive_download(cover_source.local_path, source_shas.get('cover'))
        if not file_work:
            if not cover_source:
                raise ValueError('Cover-only workflow requires an explicit cover source')
            cover_res = cover_service.process_book(
                str(biblionumber), None, os.path.dirname(cover_source.local_path),
                cover_source_path=cover_source.local_path,
            )
            cover_url = _resolve_cover_url(koha, biblionumber, cover_res)
            if not cover_url or koha.set_cover_url(biblionumber, cover_url) is not True:
                raise RuntimeError('Koha cover write-back was not confirmed')
            return {'status': 'cover_updated'}
        primary_source = source_resolver.resolve_primary(file_rel_path)
        if 'file' in checks:
            primary_source = source_resolver.materialize(primary_source, metadata=checks['file'].metadata)
        else:
            primary_source = source_resolver.materialize(primary_source)
        if strict and primary_source and primary_source.source_type == 'gdrive':
            _verify_drive_download(primary_source.local_path, source_shas.get('file'))
        cover_source_path = cover_source.local_path if cover_source else None
        original_full_path = primary_source.local_path if primary_source else None

        if not original_full_path or not os.path.exists(original_full_path):
            if cover_source_path:
                cover_res = cover_service.process_book(
                    str(biblionumber),
                    None,
                    os.path.dirname(cover_source_path),
                    cover_source_path=cover_source_path,
                )
                _resolve_cover_url(koha, biblionumber, cover_res, update_koha=True)
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

        if primary_source.lifecycle_policy == "local_managed":
            file_service = FileService()
            current_active_path = file_service.version_and_move(
                original_full_path, biblionumber
            )
        else:
            current_active_path = original_full_path
        current_lifecycle_policy = primary_source.lifecycle_policy

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
                cover_source_path=cover_source_path,
            ) if cover_work else None

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
                dpi=dpi,
                upload_name=primary_source.original_name,
                replace_existing=bool(
                    strict and checks.get('file')
                    and checks['file'].action in ('resource_changed', 'resume')
                ),
                result_callback=result_callback,
            )

            logger.info("⚡ [Core] Parallel tasks started: Cover + DSpace")

            # Check Critical Task (DSpace)
            dspace_error = None
            try:
                dspace_result = future_dspace.result()
            except Exception as e:
                logger.error(f"❌ [Core] DSpace Thread failed: {e}")
                dspace_error = e

            # Check Bonus Task (Cover)
            try:
                # CoverService has its own Poppler/HTTP timeouts and retry guard.
                cover_res = future_cover.result() if future_cover else {'status': 'skipped'}
                logger.info(f"🖼️ [Core] Cover result: {cover_res}")
                cover_url = _resolve_cover_url(
                    koha,
                    biblionumber,
                    cover_res,
                    update_koha=dspace_error is not None,
                ) if future_cover else None
                if strict and future_cover and (
                    not (cover_res.get('status') == 'success' or (
                        cover_res.get('status') == 'skipped' and cover_res.get('reason') == 'exists_in_koha'
                    )) or not cover_url
                ):
                    raise RuntimeError('Cover processing was not confirmed')

            except concurrent.futures.TimeoutError:
                logger.warning("⚠️ [Core] Cover generation timeout.")
                if strict:
                    raise
            except Exception as e:
                logger.warning(f"⚠️ [Core] Cover Thread warning: {e}")
                if strict:
                    raise

            if dspace_error is not None:
                raise dspace_error

        # --- 3. FINALIZE ---
        if strict and not dspace_result:
            raise RuntimeError('DSpace processing was not confirmed')
        if dspace_result:
            if strict and dspace_result.get('additional_files_failed'):
                raise RuntimeError('Additional file processing was not confirmed')
            if strict and dspace_result.get('status') == 'linked_existing' and (
                checks.get('file') and checks['file'].action in ('resource_changed', 'resume')
            ):
                raise RuntimeError('Changed Drive PDF requires DSpace bitstream replacement; existing link is insufficient')
            confirmed = koha.set_success(
                biblionumber,
                dspace_result["handle"],
                item_uuid=dspace_result["uuid"],
                cover_url=cover_url,
                primary_download_url=dspace_result.get("primary_download_url"),
            ) if write_back else True
            if strict and confirmed is not True:
                raise RuntimeError('Koha write-back was not confirmed')

        return dspace_result

    except Exception as e:
        logger.error(f"❌ [Core] Logic Error processing #{biblionumber}: {e}")
        try:
            koha.set_status(biblionumber, "error", str(e))
        except Exception:
            pass

        if (
            current_lifecycle_policy == "local_managed"
            and current_active_path
            and os.path.exists(current_active_path)
        ):
            file_service = FileService()
            file_service.move_to_error(current_active_path)
        raise e
