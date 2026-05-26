"""JSON logger з run_id correlation для Koha export."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

RUN_ID_NOT_SET = "-"
EXPORT_LOGGER_NAME = "KDV-Export"
REDACTED = "REDACTED"

_run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "export_run_id", default=RUN_ID_NOT_SET
)

_RESERVED_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(secret|token|password|authorization|client_secret)", re.IGNORECASE
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?P<key>GRAPH_CLIENT_SECRET|client_secret|access_token|refresh_token|"
    r"authorization|password)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


def set_run_id(run_id: str) -> contextvars.Token[str]:
    return _run_id_var.set(run_id or RUN_ID_NOT_SET)


def reset_run_id(token: contextvars.Token[str]) -> None:
    _run_id_var.reset(token)


def get_run_id() -> str:
    return _run_id_var.get()


class ExportJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "event": _normalize_event(_record_event(record)),
            "run_id": _sanitize_value(getattr(record, "run_id", get_run_id())),
        }

        for key, value in _extra_fields(record).items():
            if key in {"event", "run_id"}:
                continue
            payload[key] = _sanitize_value(value, key=key)

        if record.exc_info:
            payload["exception"] = _sanitize_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_export_logging(
    level: int = logging.INFO, stream: TextIO | None = None
) -> logging.Logger:
    logger = logging.getLogger(EXPORT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(ExportJsonFormatter())
    logger.addHandler(handler)
    return logger


def get_export_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(EXPORT_LOGGER_NAME)
    return logging.getLogger(f"{EXPORT_LOGGER_NAME}.{name}")


def _record_event(record: logging.LogRecord) -> str:
    explicit_event = getattr(record, "event", "")
    if explicit_event:
        return str(explicit_event)
    return str(record.getMessage())


def _normalize_event(event: str) -> str:
    safe_event = _sanitize_text(event)
    if safe_event.startswith("gdrive_upload_"):
        return "gdrive_copy_" + safe_event.removeprefix("gdrive_upload_")
    if safe_event.startswith("smtp_"):
        return "graph_email_" + safe_event.removeprefix("smtp_")
    return safe_event


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_LOG_RECORD_KEYS and not key.startswith("_")
    }


def _sanitize_value(value: Any, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_text(value: str) -> str:
    return _SENSITIVE_TEXT_RE.sub(lambda match: f"{match.group('key')}={REDACTED}", value)


def _is_sensitive_key(key: str) -> bool:
    return bool(key and _SENSITIVE_KEY_RE.search(key))
