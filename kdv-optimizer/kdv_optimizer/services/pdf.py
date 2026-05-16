import shutil
import subprocess
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

from kdv_optimizer.config import OptimizerConfig


config = OptimizerConfig()
_optimizer_pool = ProcessPoolExecutor(max_workers=1)

PDFINFO_TIMEOUT_SECONDS = 10
SIZE_RULE_MIN_BYTES = 50 * 1024 * 1024
SIZE_RULE_ALWAYS_BYTES = 100 * 1024 * 1024
SPECIFIC_WEIGHT_MIN_BYTES_PER_PAGE = 500 * 1024
DISK_SPACE_MULTIPLIER = 2.5


def build_job_paths(job_id: str) -> tuple[str, str]:
    safe_id = str(uuid.UUID(job_id))
    return (
        str(Path(config.INPUT_DIR) / f"{safe_id}.pdf"),
        str(Path(config.OUTPUT_DIR) / f"{safe_id}.pdf"),
    )


def needs_optimization(filepath: str, skip: bool) -> bool:
    if skip:
        return False

    try:
        file_size = Path(filepath).stat().st_size
    except OSError:
        return False

    if file_size > SIZE_RULE_ALWAYS_BYTES:
        return True

    if file_size <= SIZE_RULE_MIN_BYTES:
        return False

    try:
        pages = _count_pages_with_pdfinfo(filepath)
    except Exception:
        return True

    if pages <= 0:
        return True

    return (file_size / pages) > SPECIFIC_WEIGHT_MIN_BYTES_PER_PAGE


def _count_pages_with_pdfinfo(filepath: str) -> int:
    result = subprocess.run(
        ["pdfinfo", filepath],
        check=True,
        timeout=PDFINFO_TIMEOUT_SECONDS,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())

    raise ValueError("pdfinfo output does not contain Pages")


def _check_disk_space(filepath: str) -> bool:
    try:
        input_path = Path(filepath)
        file_size = input_path.stat().st_size
        usage_path = (
            input_path.parent if input_path.parent.exists() else Path(config.DATA_DIR)
        )
        free_bytes = shutil.disk_usage(usage_path).free
    except OSError:
        return False

    return free_bytes > int(file_size * DISK_SPACE_MULTIPLIER)


def run_ghostscript(input_path: str, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "nice",
            "-n",
            "15",
            "ionice",
            "-c",
            "3",
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",
            "-dSAFER",
            "-dNOPAUSE",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            input_path,
        ],
        check=True,
        timeout=config.GS_TIMEOUT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _mb(size_bytes: int) -> float:
    return round(size_bytes / (1024 * 1024), 2)


def _error_result(
    reason: str, input_path: str, output_path: str, started_at: float
) -> dict[str, Any]:
    original_size = Path(input_path).stat().st_size if Path(input_path).exists() else 0
    return {
        "status": "error",
        "output_path": input_path,
        "stats": {
            "engine": "ghostscript_ebook",
            "fallback_reason": reason,
            "original_mb": _mb(original_size),
            "final_mb": _mb(original_size),
            "reduction_pct": 0.0,
            "time_ms": int((time.perf_counter() - started_at) * 1000),
            "candidate_output_path": output_path,
        },
    }


def _optimize_pdf(input_path: str, output_path: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    original_size = Path(input_path).stat().st_size

    run_ghostscript(input_path, output_path)

    output = Path(output_path)
    if not output.exists():
        return _error_result("missing_output", input_path, output_path, started_at)

    optimized_size = output.stat().st_size
    if optimized_size <= 0:
        return _error_result("empty_output", input_path, output_path, started_at)

    if optimized_size > original_size:
        return _error_result("larger_output", input_path, output_path, started_at)

    reduction_pct = round((1 - (optimized_size / original_size)) * 100, 2)
    return {
        "status": "done",
        "output_path": output_path,
        "stats": {
            "engine": "ghostscript_ebook",
            "fallback_reason": None,
            "original_mb": _mb(original_size),
            "final_mb": _mb(optimized_size),
            "reduction_pct": reduction_pct,
            "time_ms": int((time.perf_counter() - started_at) * 1000),
        },
    }


class PDFOptimizerService:
    def submit_job(self, job_id: str) -> Future:
        input_path, output_path = build_job_paths(job_id)

        if not _check_disk_space(input_path):
            raise RuntimeError("not enough disk space for PDF optimization")

        return _optimizer_pool.submit(_optimize_pdf, input_path, output_path)

    def get_job_status(self, job_id: str, future: Future) -> dict[str, Any]:
        build_job_paths(job_id)

        if not future.done():
            return {"status": "processing"}

        try:
            return future.result()
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "error",
                "output_path": None,
                "stats": {
                    "engine": "ghostscript_ebook",
                    "fallback_reason": "timeout",
                    "exception": str(exc),
                },
            }
        except Exception as exc:
            return {
                "status": "error",
                "output_path": None,
                "stats": {
                    "engine": "ghostscript_ebook",
                    "fallback_reason": "exception",
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            }
