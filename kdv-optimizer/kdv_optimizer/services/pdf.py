import logging
import shutil
import subprocess
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

from kdv_optimizer.config import OptimizerConfig


logger = logging.getLogger("KDV-Optimizer")
config = OptimizerConfig()
_optimizer_pool = ProcessPoolExecutor(max_workers=1)

PDFINFO_TIMEOUT_SECONDS = 10
SIZE_RULE_MIN_BYTES = 50 * 1024 * 1024
SIZE_RULE_ALWAYS_BYTES = 100 * 1024 * 1024
SPECIFIC_WEIGHT_MIN_BYTES_PER_PAGE = 500 * 1024
DISK_SPACE_MULTIPLIER = 2.5
SUPPORTED_DPI = (100, 110, 120, 130, 140, 150)


def validate_dpi(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value not in SUPPORTED_DPI:
        raise ValueError(f"dpi must be one of {', '.join(map(str, SUPPORTED_DPI))}")
    return value


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
        logger.warning("Optimizer disk preflight failed: input_path=%s", filepath)
        return False

    required_bytes = int(file_size * DISK_SPACE_MULTIPLIER)
    enough_space = free_bytes > required_bytes
    logger.info(
        "Optimizer disk preflight: input_path=%s free_mb=%s required_mb=%s ok=%s",
        filepath,
        _mb(free_bytes),
        _mb(required_bytes),
        enough_space,
    )
    return enough_space


def run_ghostscript(
    input_path: str, output_path: str, dpi: int | None = None
) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dpi = validate_dpi(dpi)
    command = ["nice", "-n", "15", "ionice", "-c", "3"]
    if dpi is not None:
        command.extend(
            ["prlimit", f"--fsize={Path(input_path).stat().st_size}", "--"]
        )
        command.extend(
            [
                "gs",
                "-sDEVICE=pdfimage24",
                f"-r{dpi}",
                "-sCompression=JPEG",
                "-dJPEGQ=85",
                "-dUseCropBox",
            ]
        )
    else:
        command.extend(
            [
                "gs",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/ebook",
            ]
        )
    command.extend(
        [
            "-dSAFER",
            "-dNOPAUSE",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            input_path,
        ]
    )
    logger.info(
        "Ghostscript optimization started: input_path=%s output_path=%s "
        "dpi=%s timeout=%s",
        input_path,
        output_path,
        dpi,
        config.GS_TIMEOUT,
    )
    subprocess.run(
        command,
        check=True,
        timeout=config.GS_TIMEOUT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _mb(size_bytes: int) -> float:
    return round(size_bytes / (1024 * 1024), 2)


def _error_result(
    reason: str,
    input_path: str,
    output_path: str,
    started_at: float,
    dpi: int | None = None,
) -> dict[str, Any]:
    original_size = Path(input_path).stat().st_size if Path(input_path).exists() else 0
    return {
        "status": "error",
        "output_path": input_path,
        "stats": {
            "engine": "ghostscript_pdfimage24" if dpi else "ghostscript_ebook",
            "raster_dpi": dpi,
            "fallback_reason": reason,
            "original_mb": _mb(original_size),
            "final_mb": _mb(original_size),
            "reduction_pct": 0.0,
            "time_ms": int((time.perf_counter() - started_at) * 1000),
            "candidate_output_path": output_path,
        },
    }


def _optimize_pdf(
    input_path: str, output_path: str, dpi: int | None = None
) -> dict[str, Any]:
    dpi = validate_dpi(dpi)
    started_at = time.perf_counter()
    original_size = Path(input_path).stat().st_size
    expected_pages = _count_pages_with_pdfinfo(input_path) if dpi is not None else None
    if dpi is not None and expected_pages <= 0:
        return _error_result("exception", input_path, output_path, started_at, dpi)
    logger.info(
        "PDF optimization process started: input_path=%s output_path=%s original_mb=%s",
        input_path,
        output_path,
        _mb(original_size),
    )

    try:
        if dpi is None:
            run_ghostscript(input_path, output_path)
        else:
            run_ghostscript(input_path, output_path, dpi=dpi)
    except subprocess.CalledProcessError:
        output = Path(output_path)
        if dpi is not None and output.exists() and output.stat().st_size >= original_size:
            return _error_result(
                "larger_output", input_path, output_path, started_at, dpi
            )
        if dpi is not None:
            return _error_result("exception", input_path, output_path, started_at, dpi)
        raise

    output = Path(output_path)
    if not output.exists():
        logger.warning(
            "PDF optimization failed: reason=missing_output input_path=%s "
            "output_path=%s time_ms=%s",
            input_path,
            output_path,
            int((time.perf_counter() - started_at) * 1000),
        )
        return _error_result("missing_output", input_path, output_path, started_at, dpi)

    optimized_size = output.stat().st_size
    if optimized_size <= 0:
        logger.warning(
            "PDF optimization failed: reason=empty_output input_path=%s "
            "output_path=%s time_ms=%s",
            input_path,
            output_path,
            int((time.perf_counter() - started_at) * 1000),
        )
        return _error_result("empty_output", input_path, output_path, started_at, dpi)

    if optimized_size > original_size:
        logger.warning(
            "PDF optimization failed: reason=larger_output input_path=%s "
            "output_path=%s original_mb=%s final_mb=%s time_ms=%s",
            input_path,
            output_path,
            _mb(original_size),
            _mb(optimized_size),
            int((time.perf_counter() - started_at) * 1000),
        )
        return _error_result("larger_output", input_path, output_path, started_at, dpi)

    if dpi is not None:
        try:
            output_pages = _count_pages_with_pdfinfo(output_path)
        except Exception:
            return _error_result("exception", input_path, output_path, started_at, dpi)
        if output_pages != expected_pages:
            return _error_result("exception", input_path, output_path, started_at, dpi)

    reduction_pct = round((1 - (optimized_size / original_size)) * 100, 2)
    time_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "PDF optimization process completed: input_path=%s output_path=%s "
        "original_mb=%s final_mb=%s reduction_pct=%s time_ms=%s",
        input_path,
        output_path,
        _mb(original_size),
        _mb(optimized_size),
        reduction_pct,
        time_ms,
    )
    return {
        "status": "done",
        "output_path": output_path,
        "stats": {
            "engine": "ghostscript_pdfimage24" if dpi else "ghostscript_ebook",
            "raster_dpi": dpi,
            "fallback_reason": None,
            "original_mb": _mb(original_size),
            "final_mb": _mb(optimized_size),
            "reduction_pct": reduction_pct,
            "time_ms": time_ms,
        },
    }


class PDFOptimizerService:
    def submit_job(self, job_id: str, dpi: int | None = None) -> Future:
        dpi = validate_dpi(dpi)
        input_path, output_path = build_job_paths(job_id)

        if not _check_disk_space(input_path):
            logger.warning(
                "Optimizer job rejected by disk preflight: job_id=%s input_path=%s",
                job_id,
                input_path,
            )
            raise RuntimeError("not enough disk space for PDF optimization")

        logger.info(
            "Optimizer job queued: job_id=%s input_path=%s output_path=%s",
            job_id,
            input_path,
            output_path,
        )
        if dpi is None:
            return _optimizer_pool.submit(_optimize_pdf, input_path, output_path)
        return _optimizer_pool.submit(_optimize_pdf, input_path, output_path, dpi)

    def get_job_status(
        self, job_id: str, future: Future, dpi: int | None = None
    ) -> dict[str, Any]:
        build_job_paths(job_id)

        if not future.done():
            return {"status": "processing"}

        try:
            return future.result()
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "Optimizer job timed out: job_id=%s timeout=%s error=%s",
                job_id,
                config.GS_TIMEOUT,
                exc,
            )
            return {
                "status": "error",
                "output_path": None,
                "stats": {
                    "engine": "ghostscript_pdfimage24" if dpi else "ghostscript_ebook",
                    "raster_dpi": dpi,
                    "fallback_reason": "timeout",
                    "exception": str(exc),
                },
            }
        except Exception as exc:
            logger.exception("Optimizer job raised exception: job_id=%s", job_id)
            return {
                "status": "error",
                "output_path": None,
                "stats": {
                    "engine": "ghostscript_pdfimage24" if dpi else "ghostscript_ebook",
                    "raster_dpi": dpi,
                    "fallback_reason": "exception",
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            }
