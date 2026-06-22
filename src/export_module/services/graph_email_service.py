"""Надсилання Koha export email через Microsoft Graph sendMail."""

from __future__ import annotations

import base64
import html
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

import requests

from src.export_module.config import ExportConfig
from src.export_module.services.drive_mount_service import DriveMountCopyResult

LOGGER = logging.getLogger(__name__)
RETRYABLE_GRAPH_STATUS_CODES = {429, 500, 502, 503, 504}
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphEmailServiceError(RuntimeError):
    """Помилка Graph email service без витоку token або secret."""


@dataclass(frozen=True)
class GraphEmailSendResult:
    recipient: str
    attachment_included: bool
    attachment_size_bytes: int
    message_id: str | None = None


class GraphEmailService:
    def __init__(
        self,
        config: ExportConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.sleep = sleep

    def send_via_graph(
        self,
        records: list[dict],
        drive_result: DriveMountCopyResult,
        xlsx_path: str,
        run_id: str,
    ) -> GraphEmailSendResult:
        attachment_size = os.path.getsize(xlsx_path)
        include_attachment = attachment_size <= self.config.max_attachment_bytes
        token = self._get_access_token()
        payload = self._build_send_mail_payload(
            records=records,
            xlsx_path=xlsx_path,
            include_attachment=include_attachment,
            attachment_size=attachment_size,
        )
        response = self._post_send_mail_with_retry(token, payload)

        return GraphEmailSendResult(
            recipient=", ".join(_recipient_addresses(self.config.graph_to)),
            attachment_included=include_attachment,
            attachment_size_bytes=attachment_size,
            message_id=response.headers.get("request-id"),
        )

    def _get_access_token(self) -> str:
        token_url = (
            f"https://login.microsoftonline.com/{self.config.graph_tenant_id}"
            "/oauth2/v2.0/token"
        )
        response = self.session.post(
            token_url,
            data={
                "client_id": self.config.graph_client_id,
                "client_secret": self.config.graph_client_secret,
                "scope": GRAPH_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise GraphEmailServiceError(
                f"Graph token request failed: HTTP {response.status_code}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise GraphEmailServiceError(
                "Graph token response did not include access_token"
            )
        return token

    def _post_send_mail_with_retry(
        self, token: str, payload: dict[str, Any]
    ) -> requests.Response:
        url = (
            "https://graph.microsoft.com/v1.0/users/"
            f"{self.config.graph_sender_user_id}/sendMail"
        )
        max_attempts = max(1, self.config.max_retries + 1)
        last_response: requests.Response | None = None
        for attempt in range(1, max_attempts + 1):
            response = self.session.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if response.status_code < 400:
                return response

            last_response = response
            if response.status_code not in RETRYABLE_GRAPH_STATUS_CODES:
                break
            if attempt < max_attempts:
                LOGGER.warning(
                    "graph_email_retry",
                    extra={"status_code": response.status_code, "attempt": attempt},
                )
                self.sleep(_retry_delay_seconds(response, attempt))

        status_code = last_response.status_code if last_response else "unknown"
        raise GraphEmailServiceError(f"Graph sendMail failed: HTTP {status_code}")

    def _build_send_mail_payload(
        self,
        records: list[dict],
        xlsx_path: str,
        include_attachment: bool,
        attachment_size: int,
    ) -> dict[str, Any]:
        html_body = _build_html_body(
            records=records,
            include_attachment=include_attachment,
            attachment_size=attachment_size,
        )
        message: dict[str, Any] = {
            "subject": "Koha export",
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": address}}
                for address in _recipient_addresses(self.config.graph_to)
            ],
        }
        if include_attachment:
            message["attachments"] = [_build_file_attachment(xlsx_path)]
        return {"message": message, "saveToSentItems": True}


def _build_file_attachment(xlsx_path: str) -> dict[str, str]:
    path = Path(xlsx_path)
    content = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": path.name,
        "contentType": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        "contentBytes": content,
    }


def _build_html_body(
    records: list[dict],
    include_attachment: bool,
    attachment_size: int,
) -> str:
    warning = ""
    if not include_attachment:
        warning = (
            "<p><strong>Увага:</strong> XLSX перевищує ліміт вкладення "
            "і не прикріплений до листа.</p>"
        )

    items = "".join(_biblio_id_item(record) for record in records[:50])
    if not items:
        items = "<li>Немає записів для відображення</li>"

    return (
        "<html><body>"
        f"<p>Кількість експортованих записів: {len(records)}</p>"
        f"<p>Розмір XLSX: {attachment_size} bytes</p>"
        f"{warning}"
        "<p>Експортовані biblio_id:</p>"
        f"<ul>{items}</ul>"
        "</body></html>"
    )


def _biblio_id_item(record: dict) -> str:
    biblio_id = _first_value(record, "biblio_id", "biblionumber", "ID Запису", "koha_id")
    return f"<li>{html.escape(str(biblio_id or ''))}</li>"


def _recipient_addresses(graph_to: str) -> list[str]:
    recipients = [address.strip() for address in graph_to.split(",") if address.strip()]
    if not recipients:
        raise GraphEmailServiceError("GRAPH_TO did not include any recipients")
    return recipients


def _first_value(record: dict, *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in {None, ""}:
            return value
    return ""


def _retry_delay_seconds(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return min(float(attempt), 5.0)
