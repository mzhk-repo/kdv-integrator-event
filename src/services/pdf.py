import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


logger = logging.getLogger(__name__)

SIZE_RULE_A_MB = 50
SIZE_RULE_A_KB_PER_PAGE = 500
SIZE_RULE_B_MB = 100
PDFINFO_TIMEOUT_SECONDS = 10


def count_pages_with_pdfinfo(filepath: str) -> int:
    result = subprocess.run(
        ["pdfinfo", filepath],
        text=True,
        capture_output=True,
        timeout=PDFINFO_TIMEOUT_SECONDS,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError("pdfinfo_pages_missing")


def needs_optimization(filepath: str, skip: bool) -> bool:
    if skip:
        return False

    try:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
        if size_mb > SIZE_RULE_B_MB:
            return True
        if size_mb <= SIZE_RULE_A_MB:
            return False

        pages = count_pages_with_pdfinfo(filepath)
        if pages <= 0:
            return True
        return (size_mb * 1024) / pages > SIZE_RULE_A_KB_PER_PAGE
    except Exception as exc:
        logger.warning("pdf_page_count_failed: %s", exc)
        return True


def has_optimizer_disk_space(filepath: str, data_dir: str = "/data/kdv_optimize") -> bool:
    try:
        file_size = os.path.getsize(filepath)
        required = file_size * 2.5
        free = shutil.disk_usage(data_dir).free
        if free < required:
            logger.warning(
                "pdf_skip_no_disk_space free_mb=%s required_mb=%s",
                round(free / 1024 / 1024, 1),
                round(required / 1024 / 1024, 1),
            )
            return False
        return True
    except Exception as exc:
        logger.warning("pdf_disk_check_failed: %s", exc)
        return False


@dataclass
class OptimizeResult:
    success: bool
    path: str
    fallback_reason: str | None
    original_mb: float | None = None
    optimized_mb: float | None = None
    optimization_time_ms: int | None = None
    thread_wait_ms: int | None = None


class PDFOptimizerClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 130,
        poll_interval: float = 2.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._client = session or requests.Session()

    def optimize(self, original_path: str, job_id: str) -> OptimizeResult:
        try:
            logger.info(
                "Sending PDF optimization request: job_id=%s optimizer_url=%s "
                "original_path=%s original_mb=%s",
                job_id,
                self.base_url,
                original_path,
                self._file_mb(original_path),
            )
            post_resp = self._client.post(
                f"{self.base_url}/optimize",
                json={"job_id": job_id},
                timeout=self.timeout,
            )
            if post_resp.status_code >= 500:
                logger.warning(
                    "PDF optimizer returned server error on submit: job_id=%s "
                    "status_code=%s",
                    job_id,
                    post_resp.status_code,
                )
                return self._fallback(original_path, "optimizer_unavailable")
            if post_resp.status_code >= 400:
                reason = self._reason_from_response(post_resp)
                logger.warning(
                    "PDF optimizer rejected submit: job_id=%s status_code=%s "
                    "reason=%s",
                    job_id,
                    post_resp.status_code,
                    reason,
                )
                return self._fallback(original_path, reason)

            started_at = time.monotonic()
            logger.info(
                "PDF optimizer accepted job: job_id=%s status_code=%s timeout=%s",
                job_id,
                post_resp.status_code,
                self.timeout,
            )
            while (time.monotonic() - started_at) < self.timeout:
                get_resp = self._client.get(
                    f"{self.base_url}/optimize/{job_id}",
                    timeout=self.timeout,
                )
                if get_resp.status_code >= 500:
                    logger.warning(
                        "PDF optimizer returned server error while polling: "
                        "job_id=%s status_code=%s",
                        job_id,
                        get_resp.status_code,
                    )
                    return self._fallback(original_path, "optimizer_unavailable")
                if get_resp.status_code >= 400:
                    reason = self._reason_from_response(get_resp)
                    logger.warning(
                        "PDF optimizer polling failed: job_id=%s status_code=%s "
                        "reason=%s",
                        job_id,
                        get_resp.status_code,
                        reason,
                    )
                    return self._fallback(original_path, reason)

                data = get_resp.json()
                status = data.get("status")
                if status == "done":
                    logger.info("PDF optimizer reported success: job_id=%s", job_id)
                    return self._validate_and_build_result(data, original_path)
                if status == "error":
                    reason = self._reason_from_payload(data)
                    logger.warning(
                        "PDF optimizer reported error: job_id=%s reason=%s",
                        job_id,
                        reason,
                    )
                    return self._fallback(original_path, reason)

                time.sleep(self.poll_interval)

            logger.warning("PDF optimizer polling timeout: job_id=%s", job_id)
            return self._fallback(original_path, "timeout")
        except requests.exceptions.ConnectionError as exc:
            logger.warning("PDF optimizer unavailable: %s", exc)
            return self._fallback(original_path, "optimizer_unavailable")
        except requests.exceptions.Timeout as exc:
            logger.warning("PDF optimizer request timeout: %s", exc)
            return self._fallback(original_path, "timeout")
        except Exception as exc:
            logger.warning("PDF optimizer exception: %s", exc)
            return self._fallback(original_path, "exception")

    def _validate_and_build_result(
        self, data: dict[str, Any], original_path: str
    ) -> OptimizeResult:
        output_path = data.get("output_path")
        if not isinstance(output_path, str) or not output_path:
            return self._fallback(original_path, "empty_output")

        output = Path(output_path)
        original = Path(original_path)
        if not output.is_file():
            return self._fallback(original_path, "empty_output")

        output_size = output.stat().st_size
        if output_size <= 0:
            return self._fallback(original_path, "empty_output")

        if original.is_file() and output_size > original.stat().st_size:
            return self._fallback(original_path, "larger_output")

        stats = data.get("stats") or {}
        return OptimizeResult(
            success=True,
            path=output_path,
            fallback_reason=None,
            original_mb=stats.get("original_mb"),
            optimized_mb=stats.get("final_mb") or stats.get("optimized_mb"),
            optimization_time_ms=stats.get("time_ms")
            or stats.get("optimization_time_ms"),
            thread_wait_ms=stats.get("thread_wait_ms"),
        )

    def _fallback(self, original_path: str, reason: str) -> OptimizeResult:
        return OptimizeResult(
            success=False,
            path=original_path,
            fallback_reason=reason,
            original_mb=self._file_mb(original_path),
        )

    def _reason_from_response(self, response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return "exception"
        return self._reason_from_payload(data)

    def _reason_from_payload(self, data: dict[str, Any]) -> str:
        stats = data.get("stats") or {}
        reason = stats.get("fallback_reason") or data.get("reason")
        if isinstance(reason, str) and reason:
            return reason
        return "exception"

    def _file_mb(self, path: str) -> float | None:
        try:
            return round(Path(path).stat().st_size / (1024 * 1024), 2)
        except OSError:
            return None
