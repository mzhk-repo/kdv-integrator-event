"""Drive metadata/SHA gate for the external cover and PDF pipelines."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from src.cover_state.state_machine import StateMachine
from src.services.sources import GoogleDriveSource

logger = logging.getLogger("KDV-CoverSource")


class DriveMetadataError(RuntimeError):
    pass


class MissingChecksumError(DriveMetadataError):
    pass


@dataclass(frozen=True)
class DriveCheckResult:
    action: Literal["noop", "no_source", "resume", "deferred", "same_content", "resource_changed"]
    sha256: str | None = None
    metadata: dict | None = None


def check_drive_metadata(
    state: StateMachine,
    record_uid: str,
    incoming_file_id: str | None,
    *,
    source: Literal["cover", "file"],
    resource_key: str | None = None,
    drive_source: GoogleDriveSource | None = None,
    retry_checked: bool = False,
    force_refresh: bool = False,
) -> DriveCheckResult:
    """Gate Drive calls, compare SHA and persist failures without downloads.

    Callers serialize complete record cycles. Changed source identities/hashes
    remain uncommitted until downstream success; only confirmed same-content
    identity updates are persisted here.
    retry_checked is only for a locked caller that validated eligibility once
    before checking all sources in that cycle.
    """
    decision = state.check_source(record_uid, incoming_file_id, source=source)
    if decision == "no_source" or (decision == "noop" and not force_refresh):
        return DriveCheckResult(decision)
    record = state.get(record_uid)
    if not retry_checked and record is not None and record["status"] != "ok" and not state.is_retry_eligible(record_uid):
        return DriveCheckResult("deferred")
    if decision == "resume":
        return DriveCheckResult("resume")

    try:
        drive = drive_source if drive_source is not None else GoogleDriveSource()
        metadata = drive.get_metadata(incoming_file_id, resource_key)
        if not isinstance(metadata, dict):
            raise ValueError("Drive metadata must be an object")
    except Exception as error:
        if state.mark_pending(record_uid):
            state.record_result(record_uid, success=False)
        # Do not log API exception text: it can contain credential-bearing URLs.
        logger.warning("record_uid=%r source=%s Drive metadata request failed (%s)",
                       record_uid, source, type(error).__name__)
        raise DriveMetadataError("Drive metadata request failed") from None

    checksum = metadata.get("sha256Checksum")
    if not isinstance(checksum, str) or re.fullmatch(r"[a-fA-F0-9]{64}", checksum) is None:
        reason = "missing sha256Checksum" if checksum is None or checksum == "" else "invalid sha256Checksum"
        if state.mark_pending(record_uid):
            state.record_result(record_uid, success=False, permanent=True)
        logger.error("record_uid=%r source=%s %s; manual reset required", record_uid, source, reason)
        raise MissingChecksumError(reason)
    checksum = checksum.lower()
    if state.update_source_id(record_uid, incoming_file_id, checksum, source=source):
        return DriveCheckResult(
            "resource_changed" if force_refresh else "same_content", checksum, metadata
        )
    if not state.mark_pending(record_uid):
        return DriveCheckResult("deferred")
    # Matching SHA in an unfinished cycle still needs reconciliation.
    action = "resume" if record is not None and record[f"{source}_source_sha256"] == checksum else "resource_changed"
    return DriveCheckResult(action, checksum, metadata)
