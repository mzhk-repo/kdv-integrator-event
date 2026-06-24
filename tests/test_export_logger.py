import io
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.observability.logger import (  # noqa: E402
    ExportJsonFormatter,
    get_run_id,
    reset_run_id,
    set_run_id,
)


def _logger_with_stream(stream):
    logger = logging.getLogger("test-export-json-logger")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ExportJsonFormatter())
    logger.addHandler(handler)
    return logger


def _read_json_line(stream):
    return json.loads(stream.getvalue().strip().splitlines()[-1])


def test_json_log_line_contains_required_fields_with_context_run_id():
    stream = io.StringIO()
    logger = _logger_with_stream(stream)
    token = set_run_id("run-123")
    try:
        logger.info("export_started")
    finally:
        reset_run_id(token)

    payload = _read_json_line(stream)

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "INFO"
    assert payload["event"] == "export_started"
    assert payload["run_id"] == "run-123"
    assert get_run_id() == "-"


def test_secret_token_and_authorization_fields_are_redacted():
    stream = io.StringIO()
    logger = _logger_with_stream(stream)

    logger.info(
        "graph_email_failed GRAPH_CLIENT_SECRET=raw-secret",
        extra={
            "client_secret": "raw-secret",
            "access_token": "raw-token",
            "headers": {"Authorization": "Bearer raw-token"},
            "recipient": "target@example.org",
        },
    )

    raw_line = stream.getvalue()
    payload = _read_json_line(stream)

    assert "raw-secret" not in raw_line
    assert "raw-token" not in raw_line
    assert payload["client_secret"] == "REDACTED"
    assert payload["access_token"] == "REDACTED"
    assert payload["headers"]["Authorization"] == "REDACTED"
    assert payload["recipient"] == "target@example.org"
    assert payload["event"] == "graph_email_failed GRAPH_CLIENT_SECRET=REDACTED"


def test_gdrive_upload_event_is_normalized_to_gdrive_copy():
    stream = io.StringIO()
    logger = _logger_with_stream(stream)

    logger.info("gdrive_upload_started")

    assert _read_json_line(stream)["event"] == "gdrive_copy_started"


def test_smtp_event_is_normalized_to_graph_email():
    stream = io.StringIO()
    logger = _logger_with_stream(stream)

    logger.info("smtp_send_failed")

    assert _read_json_line(stream)["event"] == "graph_email_send_failed"
