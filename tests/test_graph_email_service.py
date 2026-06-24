import base64
import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.config import ExportConfig  # noqa: E402
from src.export_module.services.drive_mount_service import (  # noqa: E402
    DriveMountCopyResult,
)
from src.export_module.services.graph_email_service import (  # noqa: E402
    GraphEmailService,
    GraphEmailServiceError,
)


class _Response:
    def __init__(self, payload=None, status_code=200, headers=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("unexpected POST call")
        return self.responses.pop(0)


def _config(max_attachment_bytes=1024, max_retries=3, graph_to="target@example.org"):
    return ExportConfig(
        enabled=True,
        koha_base_url="https://koha.example.org",
        koha_api_user="koha-user",
        koha_api_password="koha-pass",
        export_gdrive_root_path="/mnt/drive/KohaExports",
        graph_tenant_id="tenant-id",
        graph_client_id="client-id",
        graph_client_secret="super-secret-value",
        graph_sender_user_id="sender@example.org",
        graph_to=graph_to,
        max_retries=max_retries,
        max_attachment_bytes=max_attachment_bytes,
    )


def _drive_result():
    return DriveMountCopyResult(
        file_path="/mnt/drive/KohaExports/2026/export.xlsx",
        folder_path="/mnt/drive/KohaExports/2026",
        file_name="export.xlsx",
    )


def _xlsx(tmp_path, payload=b"xlsx payload"):
    path = tmp_path / "export_Koha_2026-05-28_120000_run12345.xlsx"
    path.write_bytes(payload)
    return path


def _service(session, config=None):
    return GraphEmailService(config or _config(), session=session, sleep=lambda _: None)


def test_small_file_is_sent_with_graph_attachment(tmp_path):
    xlsx_path = _xlsx(tmp_path, b"small payload")
    session = _Session(
        [
            _Response({"access_token": "token-value"}),
            _Response(status_code=202, headers={"request-id": "request-1"}),
        ]
    )
    service = _service(session)

    result = service.send_via_graph(
        records=[
            {"biblio_id": 101, "Автор": "Автор", "Назва книги": "Назва"}
        ],
        drive_result=_drive_result(),
        xlsx_path=str(xlsx_path),
        run_id="run12345-0000",
    )

    send_call = session.calls[1]
    message = send_call["kwargs"]["json"]["message"]
    attachment = message["attachments"][0]
    html_body = message["body"]["content"]
    assert result.attachment_included is True
    assert result.attachment_size_bytes == len(b"small payload")
    assert result.message_id == "request-1"
    assert attachment["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert attachment["name"] == xlsx_path.name
    assert base64.b64decode(attachment["contentBytes"]) == b"small payload"
    assert message["subject"] == "Koha export"
    assert "run12345" not in message["subject"]
    assert "run_id" not in html_body
    assert "run12345-0000" not in html_body
    assert "Google Drive файл" not in html_body
    assert "Google Drive папка" not in html_body
    assert "<table>" not in html_body
    assert "Експортовані biblio_id" in html_body
    assert "<li>101</li>" in html_body


def test_large_file_is_sent_without_attachment_and_with_html_warning(tmp_path):
    xlsx_path = _xlsx(tmp_path, b"payload over limit")
    session = _Session(
        [
            _Response({"access_token": "token-value"}),
            _Response(status_code=202),
        ]
    )
    service = _service(session, config=_config(max_attachment_bytes=3))

    result = service.send_via_graph(
        records=[{"biblio_id": 101, "Назва книги": "Назва"}],
        drive_result=_drive_result(),
        xlsx_path=str(xlsx_path),
        run_id="run12345-0000",
    )

    message = session.calls[1]["kwargs"]["json"]["message"]
    html_body = message["body"]["content"]
    assert result.attachment_included is False
    assert "attachments" not in message
    assert "Увага" in html_body
    assert "/mnt/drive/KohaExports/2026/export.xlsx" not in html_body
    assert "/mnt/drive/KohaExports/2026" not in html_body
    assert "run12345-0000" not in html_body
    assert "<li>101</li>" in html_body


def test_graph_to_supports_multiple_comma_separated_recipients(tmp_path):
    xlsx_path = _xlsx(tmp_path)
    session = _Session(
        [
            _Response({"access_token": "token-value"}),
            _Response(status_code=202),
        ]
    )
    service = _service(
        session,
        config=_config(graph_to="first@example.org, second@example.org, ,third@example.org"),
    )

    result = service.send_via_graph(
        records=[{"biblio_id": 101}],
        drive_result=_drive_result(),
        xlsx_path=str(xlsx_path),
        run_id="run12345-0000",
    )

    message = session.calls[1]["kwargs"]["json"]["message"]
    assert result.recipient == "first@example.org, second@example.org, third@example.org"
    assert message["toRecipients"] == [
        {"emailAddress": {"address": "first@example.org"}},
        {"emailAddress": {"address": "second@example.org"}},
        {"emailAddress": {"address": "third@example.org"}},
    ]


def test_graph_429_retry_succeeds(tmp_path):
    xlsx_path = _xlsx(tmp_path)
    session = _Session(
        [
            _Response({"access_token": "token-value"}),
            _Response(status_code=429, headers={"Retry-After": "0"}),
            _Response(status_code=202),
        ]
    )
    service = _service(session, config=_config(max_retries=2))

    result = service.send_via_graph(
        records=[],
        drive_result=_drive_result(),
        xlsx_path=str(xlsx_path),
        run_id="run12345-0000",
    )

    assert result.attachment_included is True
    assert [call["url"] for call in session.calls].count(
        "https://graph.microsoft.com/v1.0/users/sender@example.org/sendMail"
    ) == 2


def test_secret_and_token_do_not_leak_to_logs(tmp_path, caplog):
    xlsx_path = _xlsx(tmp_path)
    session = _Session(
        [
            _Response({"access_token": "very-sensitive-token"}),
            _Response(status_code=429, headers={"Retry-After": "0"}),
            _Response(status_code=500),
        ]
    )
    service = _service(session, config=_config(max_retries=1))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(GraphEmailServiceError, match="sendMail"):
            service.send_via_graph(
                records=[],
                drive_result=_drive_result(),
                xlsx_path=str(xlsx_path),
                run_id="run12345-0000",
            )

    logs = caplog.text
    assert "very-sensitive-token" not in logs
    assert "super-secret-value" not in logs
    assert "Authorization" not in logs


def test_token_request_uses_client_credentials_without_logging_secret(tmp_path):
    xlsx_path = _xlsx(tmp_path)
    session = _Session(
        [
            _Response({"access_token": "token-value"}),
            _Response(status_code=202),
        ]
    )
    service = _service(session)

    service.send_via_graph(
        records=[],
        drive_result=_drive_result(),
        xlsx_path=str(xlsx_path),
        run_id="run12345-0000",
    )

    token_call = session.calls[0]
    assert token_call["url"] == (
        "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token"
    )
    assert token_call["kwargs"]["data"] == {
        "client_id": "client-id",
        "client_secret": "super-secret-value",
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }


def test_service_does_not_use_smtp_contract():
    source = Path("src/export_module/services/graph_email_service.py").read_text(
        encoding="utf-8"
    )

    assert "SMTP_" not in source
    assert "smtplib" not in source
