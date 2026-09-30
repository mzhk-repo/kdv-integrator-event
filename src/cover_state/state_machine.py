"""Durable cycle state for a single-writer external cover pipeline."""

from __future__ import annotations

import math
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

    def record_result(self, record_uid: str, *, success: bool, partial: bool = False) -> None:
        """Apply a confirmed cycle result; partial errors preserve pending state."""
        self._validate_uid(record_uid)
        if type(success) is not bool or type(partial) is not bool:
            raise ValueError("success and partial must be booleans")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE records
                SET status = CASE
                        WHEN ? THEN 'ok'
                        WHEN ? AND retry_count + 1 < ? THEN 'pending'
                        ELSE 'failed' END,
                    retry_count = CASE WHEN ? THEN 0 ELSE retry_count + 1 END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE record_uid = ?
                """,
                (success, partial, self.max_retry_count, success, record_uid),
            )
            if cursor.rowcount != 1:
                raise KeyError(record_uid)

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
        eligible = []
        for row in rows:
            updated_at = datetime.fromisoformat(row["updated_at"]).replace(tzinfo=timezone.utc)
            elapsed = (now - updated_at).total_seconds()
            # Compare in log space to avoid overflow for large configured limits.
            if row["retry_count"] == 0 or (
                elapsed >= 1 and row["retry_count"] - 1 <= math.log2(elapsed)
            ):
                eligible.append(dict(row))
        return eligible

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
