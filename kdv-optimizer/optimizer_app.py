import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from kdv_optimizer.config import OptimizerConfig
from kdv_optimizer.services.janitor import TTLJanitor
from kdv_optimizer.services.pdf import PDFOptimizerService, build_job_paths


_jobs: dict[str, dict[str, Any]] = {}
config = OptimizerConfig()
optimizer_service = PDFOptimizerService()


def _json_response(payload: dict[str, Any], status_code: int):
    response = jsonify(payload)
    response.status_code = status_code
    return response


def _validate_job_id(job_id: str) -> tuple[str, str]:
    return build_job_paths(job_id)


def _validate_done_result(
    job: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    if result.get("status") != "done":
        return result

    input_path = Path(job["input_path"])
    output_path_raw = result.get("output_path")
    output_path = Path(output_path_raw) if output_path_raw else None

    if output_path is None or not output_path.exists():
        return _status_error("missing_output", result)

    output_size = output_path.stat().st_size
    if output_size <= 0:
        return _status_error("empty_output", result)

    if input_path.exists() and output_size > input_path.stat().st_size:
        return _status_error("larger_output", result)

    return result


def _status_error(reason: str, result: dict[str, Any]) -> dict[str, Any]:
    stats = dict(result.get("stats") or {})
    stats["fallback_reason"] = reason
    return {
        "status": "error",
        "output_path": result.get("output_path"),
        "stats": stats,
    }


def _path_is_writable(directory: Path) -> bool:
    if not directory.exists() or not directory.is_dir():
        return False
    if not os.access(directory, os.W_OK):
        return False

    probe = directory / ".optimizer_ready_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _command_available(command: str, version_args: list[str]) -> bool:
    if shutil.which(command) is None:
        return False

    try:
        subprocess.run(
            [command, *version_args],
            check=True,
            timeout=5,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return True
    except Exception:
        return False


def _readiness_reasons() -> list[str]:
    reasons: list[str] = []

    for name, directory in (
        ("INPUT_DIR", Path(config.INPUT_DIR)),
        ("OUTPUT_DIR", Path(config.OUTPUT_DIR)),
    ):
        if not _path_is_writable(directory):
            reasons.append(f"{name} is missing or not writable: {directory}")

    if not _command_available("gs", ["--version"]):
        reasons.append("gs is not available")

    if not _command_available("pdfinfo", ["-v"]):
        reasons.append("pdfinfo is not available")

    return reasons


def create_app(start_janitor: bool = True) -> Flask:
    flask_app = Flask(__name__)

    if start_janitor:
        janitor = TTLJanitor(config)
        janitor.cleanup_once()
        janitor.start()
        flask_app.extensions["ttl_janitor"] = janitor

    @flask_app.post("/optimize")
    def start_optimize():
        payload = request.get_json(silent=True) or {}
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            return _json_response(
                {"status": "error", "reason": "job_id is required"}, 400
            )

        try:
            input_path, _ = _validate_job_id(job_id)
        except ValueError:
            return _json_response(
                {"status": "error", "reason": "invalid job_id"}, 400
            )

        if not Path(input_path).is_file():
            return _json_response(
                {"status": "error", "reason": "input_not_found"}, 404
            )

        try:
            future = optimizer_service.submit_job(job_id)
        except RuntimeError as exc:
            return _json_response(
                {"status": "error", "reason": str(exc)}, 503
            )

        _jobs[job_id] = {
            "future": future,
            "submitted_at": time.time(),
            "input_path": input_path,
        }
        return _json_response({"job_id": job_id, "status": "processing"}, 202)

    @flask_app.get("/optimize/<job_id>")
    def get_optimize_status(job_id: str):
        try:
            _validate_job_id(job_id)
        except ValueError:
            return _json_response(
                {"status": "error", "reason": "invalid job_id"}, 400
            )

        job = _jobs.get(job_id)
        if job is None:
            return _json_response(
                {"status": "error", "reason": "job_not_found"}, 404
            )

        result = optimizer_service.get_job_status(job_id, job["future"])
        return _json_response(_validate_done_result(job, result), 200)

    @flask_app.get("/health")
    def health():
        return _json_response({"status": "ok"}, 200)

    @flask_app.get("/ready")
    def ready():
        reasons = _readiness_reasons()
        if reasons:
            return _json_response(
                {"status": "not_ready", "reason": reasons}, 503
            )
        return _json_response({"status": "ready"}, 200)

    return flask_app


app = create_app()
