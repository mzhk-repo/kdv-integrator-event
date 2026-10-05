"""Dispatch due durable retries through the existing integration workflow."""

from __future__ import annotations

import logging
import os
import threading
import time

from src.cover_state.state_machine import StateMachine
from src.core import process_integration_logic
from src.tasks import task_manager

logger = logging.getLogger("KDV-RetryScheduler")


class RetryScheduler:
    def __init__(self, *, interval: float = 5.0, state_factory=StateMachine):
        self.interval = interval
        self.state_factory = state_factory
        self.state = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="cover-retry-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_once(self):
        state = self.state or self.state_factory()
        self.state = state
        for row in state.claim_due_retries():
            uid, biblionumber = row["record_uid"], row["biblionumber"]
            try:
                # Validate the durable local routing hint before dispatch.
                from src.clients.koha import KohaClientWrapper

                meta = KohaClientWrapper().get_biblio_metadata(int(biblionumber))
                if not meta or str(meta.get("record_uid", "")).lower() != uid.lower():
                    logger.error("Retry route identity mismatch record_uid=%r biblionumber=%s", uid, biblionumber)
                    continue
                task_id = task_manager.start_task(process_integration_logic, int(biblionumber))
                logger.info("Queued automatic retry record_uid=%r task_id=%s", uid, task_id)
            except Exception as error:
                logger.error("Could not queue retry record_uid=%r error_type=%s", uid, type(error).__name__)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as error:
                logger.error("Retry scan failed error_type=%s", type(error).__name__)
            self._stop.wait(self.interval)


def start_retry_scheduler():
    if not os.environ.get("COVER_STATE_DB_PATH"):
        return None
    scheduler = RetryScheduler()
    scheduler.start()
    return scheduler
