"""Durable cycle state for a single-writer external cover pipeline."""

from __future__ import annotations

import math
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Literal

from src.cover_state.schema import migrate


class StateMachine:
    """Track confirmed cycles; callers must serialize processing of each record.

    Retry delays are 1, 2, 4, ... seconds after consecutive failures. Selection
    never sleeps and never claims work for concurrent pipeline workers.
    """

    def __init__(self, db_path: str | None = None, max_retry_count: int | None = None):
        if max_retry_count is None:
            value = os.environ.get("MAX_RETRY_COUNT", "")
            try:
                max_retry_count = int(value)
            except ValueError as error:
                raise ValueError("MAX_RETRY_COUNT must be a positive integer") from error
        if type(max_retry_count) is not int or max_retry_count <= 0:
            raise ValueError("MAX_RETRY_COUNT must be a positive integer")
        self.max_retry_count = max_retry_count
        self.db_path = db_path if db_path is not None else os.environ.get("COVER_STATE_DB_PATH", "")
        migrate(self.db_path)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    @staticmethod
    def _validate_uid(record_uid: str) -> None:
        if not isinstance(record_uid, str) or not record_uid.strip():
            raise ValueError("record_uid must be a non-empty string")

    def get(self, record_uid: str) -> dict | None:
        self._validate_uid(record_uid)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM records WHERE record_uid = ?", (record_uid,)
            ).fetchone()
        return dict(row) if row is not None else None

    def check_source(
        self,
        record_uid: str,
        incoming_file_id: str | None,
        *,
        source: Literal["cover", "file"],
    ) -> Literal["noop", "needs_sha_check", "resume", "no_source"]:
        """Compare an already-parsed Drive ID without API calls or state writes.

        Call before mark_pending. Retry callers must still obey eligibility;
        resume is not permission to bypass cutoff/backoff.
        """
        self._validate_uid(record_uid)
        if source not in ("cover", "file"):
            raise ValueError("source must be 'cover' or 'file'")
        if incoming_file_id is None or incoming_file_id == "":
            return "no_source"
        if not isinstance(incoming_file_id, str) or not incoming_file_id.strip():
            raise ValueError("incoming_file_id must be a non-empty string or None")
        record = self.get(record_uid)
        if record is None or incoming_file_id != record[f"{source}_source_id"]:
            return "needs_sha_check"
        return "noop" if record["status"] == "ok" else "resume"

    def mark_pending(self, record_uid: str) -> bool:
        """Start/resume a cycle without resetting failures or durable resources.

        Returns False for an exhausted record. Retry callers select eligible
        records first; this transition does not enforce elapsed backoff.
        """
        self._validate_uid(record_uid)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO records (record_uid) VALUES (?)
                ON CONFLICT(record_uid) DO UPDATE
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE records.retry_count < ?
                """,
                (record_uid, self.max_retry_count),
            )
        return cursor.rowcount == 1

    def record_result(
        self, record_uid: str, *, success: bool, partial: bool = False, permanent: bool = False
    ) -> None:
        """Apply a confirmed cycle result; partial errors preserve pending state."""
        self._validate_uid(record_uid)
        if any(type(value) is not bool for value in (success, partial, permanent)):
            raise ValueError("success, partial and permanent must be booleans")
        if success and permanent:
            raise ValueError("a successful result cannot be a permanent failure")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE records
                SET status = CASE
                        WHEN ? THEN 'ok'
                        WHEN ? THEN 'failed'
                        WHEN ? AND retry_count + 1 < ? THEN 'pending'
                        ELSE 'failed' END,
                    retry_count = CASE WHEN ? THEN 0
                        WHEN ? THEN MAX(retry_count + 1, ?)
                        ELSE retry_count + 1 END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE record_uid = ?
                """,
                (success, permanent, partial, self.max_retry_count,
                 success, permanent, self.max_retry_count, record_uid),
            )
            if cursor.rowcount != 1:
                raise KeyError(record_uid)

            if success:
                connection.execute("DELETE FROM pending_cover_work WHERE record_uid=?", (record_uid,))

    def update_source_id(
        self, record_uid: str, file_id: str, sha256: str, *, source: Literal["cover", "file"]
    ) -> bool:
        """Remember a new ID only for confirmed, identical source content."""
        self._validate_uid(record_uid)
        if source not in ("cover", "file"):
            raise ValueError("source must be 'cover' or 'file'")
        if not isinstance(file_id, str) or not file_id.strip():
            raise ValueError("file_id must be a non-empty string")
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE records SET {source}_source_id = ?, updated_at = CURRENT_TIMESTAMP "
                f"WHERE record_uid = ? AND status = 'ok' AND {source}_source_sha256 = ?",
                (file_id, record_uid, sha256),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _retry_due(record, now: datetime) -> bool:
        updated_at = datetime.fromisoformat(record["updated_at"]).replace(tzinfo=timezone.utc)
        elapsed = (now - updated_at).total_seconds()
        # Compare in log space to avoid overflow for large configured limits.
        return record["retry_count"] == 0 or (
            elapsed >= 1 and record["retry_count"] - 1 <= math.log2(elapsed)
        )

    def is_retry_eligible(self, record_uid: str) -> bool:
        record = self.get(record_uid)
        return bool(
            record is not None and record["status"] in ("pending", "failed")
            and record["retry_count"] < self.max_retry_count
            and self._retry_due(record, datetime.now(timezone.utc))
        )

    def get_retry_eligible(self, *, now: datetime | None = None) -> list[dict]:
        """Return unfinished, unexhausted records whose backoff has elapsed."""
        now = datetime.now(timezone.utc) if now is None else now
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM records
                WHERE status IN ('pending', 'failed') AND retry_count < ?
                ORDER BY updated_at, record_uid
                """,
                (self.max_retry_count,),
            ).fetchall()
        return [dict(row) for row in rows if self._retry_due(row, now)]

    def reset_retry_count(self, record_uid: str) -> None:
        """Explicit operator reset after fixing the cause; retain status/resources."""
        self._validate_uid(record_uid)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE records SET retry_count = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE record_uid = ?", (record_uid,),
            )
            if cursor.rowcount != 1:
                raise KeyError(record_uid)

    def complete_cycle(self, record_uid: str, sources: dict, result: dict) -> None:
        """Commit source identities only after confirmed downstream write-back."""
        self._validate_uid(record_uid)
        assignments = ["status='ok'", "retry_count=0", "updated_at=CURRENT_TIMESTAMP"]
        values = []
        for source, (file_id, checksum) in sources.items():
            if source not in ("cover", "file") or not file_id or not checksum:
                raise ValueError("confirmed source ID and SHA are required")
            assignments.extend([f"{source}_source_id=?", f"{source}_source_sha256=?"])
            values.extend([file_id, checksum])
        for column, key in (("dspace_item_uuid", "uuid"), ("dspace_bitstream_uuid", "bitstream_uuid")):
            if result.get(key):
                assignments.append(f"{column}=?")
                values.append(result[key])
        if result.get("cover_asset_sha256"):
            assignments.append("cover_asset_sha256=?")
            values.append(result["cover_asset_sha256"])
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE records SET {', '.join(assignments)} WHERE record_uid=?",
                (*values, record_uid),
            )
            if cursor.rowcount != 1:
                raise KeyError(record_uid)
            connection.execute("DELETE FROM pending_cover_work WHERE record_uid=?", (record_uid,))

    def get_cover_work(self, record_uid: str) -> dict | None:
        self._validate_uid(record_uid)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pending_cover_work WHERE record_uid=?", (record_uid,)
            ).fetchone()
        if row is None:
            return None
        work = dict(row)
        work["sources"] = json.loads(work["sources"])
        work["result"] = json.loads(work["result"]) if work["result"] is not None else None
        return work

    def save_cover_work(self, record_uid: str, inputs_sha256: str, sources: dict,
                        asset_sha256: str, *, file_work: bool, result: dict | None = None) -> None:
        """Checkpoint published cover/PDF work without confirming source identities."""
        self._validate_uid(record_uid)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE records SET cover_asset_sha256=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE record_uid=? AND status='pending' AND retry_count < ?",
                (asset_sha256, record_uid, self.max_retry_count),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cover checkpoint requires an eligible pending cycle")
            connection.execute(
                "INSERT OR REPLACE INTO pending_cover_work "
                "(record_uid, inputs_sha256, sources, file_work, result, cover_asset_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record_uid, inputs_sha256, json.dumps(sources), int(file_work),
                 json.dumps(result) if result is not None else None, asset_sha256),
            )
