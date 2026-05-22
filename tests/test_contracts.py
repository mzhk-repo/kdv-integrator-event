import os

# Required config env vars before importing src modules.
os.environ.setdefault("KDV_API_TOKEN", "test-token")
os.environ.setdefault("KOHA_API_URL", "http://koha.local")
os.environ.setdefault("KOHA_OPAC_URL", "http://koha.local")
os.environ.setdefault("KOHA_API_USER", "user")
os.environ.setdefault("KOHA_API_PASS", "pass")
os.environ.setdefault("DSPACE_API_URL", "http://dspace.local/server")
os.environ.setdefault("DSPACE_UI_URL", "http://dspace.local")
os.environ.setdefault("DSPACE_API_USER", "user")
os.environ.setdefault("DSPACE_API_PASS", "pass")
os.environ.setdefault("INTEGRATOR_MOUNT_PATH", "/tmp")

from src.dspace import DSpaceClient, DSpaceRestError
from src.koha import KohaClient
import src.koha as koha_module


class _Resp:
    def __init__(self, status_code=200, payload=None, text="", url=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.url = url

    def json(self):
        return self._payload


def test_dspace_pid_find_contract_uses_expected_endpoint_and_params(monkeypatch):
    client = DSpaceClient()
    captured = {}

    def fake_request(method, endpoint, **kwargs):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["kwargs"] = kwargs
        return _Resp(status_code=200, payload={"uuid": "u-1", "type": "item"})

    monkeypatch.setattr(client, "_request", fake_request)

    item_uuid = client.find_item_uuid_by_handle("123/456")

    assert item_uuid == "u-1"
    assert captured["method"] == "GET"
    assert captured["endpoint"] == "/pid/find"
    assert captured["kwargs"]["params"] == {"id": "123/456"}


def test_dspace_update_metadata_contract_builds_json_patch(monkeypatch):
    client = DSpaceClient()
    captured = {}

    def fake_request(method, endpoint, **kwargs):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["kwargs"] = kwargs
        return _Resp(status_code=200)

    monkeypatch.setattr(client, "_request", fake_request)

    ok = client.update_metadata(
        "item-uuid",
        {
            "dc.title": "Test Book",
            "dc.contributor.author": ["Author A", "Author B"],
            "handle": "should-be-ignored",
            "uuid": "should-be-ignored",
            "dc.description": None,
        },
    )

    assert ok is True
    assert captured["method"] == "PATCH"
    assert captured["endpoint"] == "/core/items/item-uuid"
    assert captured["kwargs"]["headers"]["Content-Type"] == "application/json-patch+json"

    patch_ops = captured["kwargs"]["json"]
    assert len(patch_ops) == 2
    assert patch_ops[0]["op"] == "replace"
    assert patch_ops[0]["path"] == "/metadata/dc.title"
    assert patch_ops[0]["value"] == [{"value": "Test Book", "language": None}]
    assert patch_ops[1]["path"] == "/metadata/dc.contributor.author"
    assert patch_ops[1]["value"] == [
        {"value": "Author A", "language": None},
        {"value": "Author B", "language": None},
    ]


def test_dspace_get_primary_bitstream_reads_first_original_bitstream(monkeypatch):
    client = DSpaceClient()
    calls = []

    def fake_request(method, endpoint, **kwargs):
        calls.append((method, endpoint))
        if endpoint == "/core/items/item-uuid/bundles":
            return _Resp(
                status_code=200,
                payload={
                    "_embedded": {
                        "bundles": [{"name": "ORIGINAL", "uuid": "bundle-uuid"}]
                    }
                },
            )
        if endpoint == "/core/bundles/bundle-uuid/bitstreams":
            return _Resp(
                status_code=200,
                payload={"_embedded": {"bitstreams": [{"uuid": "bitstream-uuid"}]}},
            )
        return _Resp(status_code=404)

    monkeypatch.setattr(client, "_request", fake_request)

    bitstream = client.get_primary_bitstream("item-uuid")

    assert bitstream == {"uuid": "bitstream-uuid"}
    assert calls == [
        ("GET", "/core/items/item-uuid/bundles"),
        ("GET", "/core/bundles/bundle-uuid/bitstreams"),
    ]


def test_dspace_upload_to_item_uses_explicit_upload_name(monkeypatch, tmp_path):
    client = DSpaceClient()
    pdf = tmp_path / "11111111-1111-4111-8111-111111111111.pdf"
    pdf.write_bytes(b"pdf-bytes")
    captured = {}

    def fake_request(method, endpoint, **kwargs):
        if method == "GET" and endpoint == "/core/items/item-uuid/bundles":
            return _Resp(
                status_code=200,
                payload={
                    "_embedded": {
                        "bundles": [{"name": "ORIGINAL", "uuid": "bundle-uuid"}]
                    }
                },
            )
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["files"] = kwargs["files"]
        return _Resp(status_code=201, payload={"uuid": "bitstream-uuid"})

    monkeypatch.setattr(client, "_request", fake_request)

    ok = client.upload_to_item(
        "item-uuid", str(pdf), upload_name="Processed/biblio_73_v01.pdf"
    )

    assert ok == {"uuid": "bitstream-uuid"}
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "/core/bundles/bundle-uuid/bitstreams"
    upload_field = captured["files"]["file"]
    assert upload_field[0] == "biblio_73_v01.pdf"
    assert upload_field[2] == "application/pdf"


def test_koha_success_writes_file_856_before_handle_856(monkeypatch):
    client = KohaClient()
    captured = {}
    xml = (
        '<record><datafield tag="956" ind1=" " ind2=" ">'
        '<subfield code="u">books/book.pdf</subfield>'
        '</datafield>'
        '<datafield tag="856" ind1="4" ind2="0">'
        '<subfield code="u">old</subfield>'
        '</datafield></record>'
    )

    monkeypatch.setattr(client, "_get_biblio_xml", lambda _biblio_id: xml)

    def fake_put(url, data=None, headers=None):
        captured["data"] = data.decode("utf-8")
        return _Resp(status_code=200)

    monkeypatch.setattr(client.session, "put", fake_put)

    ok = client.set_success(
        42,
        "https://repo.pinokew.buzz/handle/123/456",
        item_uuid="item-uuid",
        primary_download_url=(
            "https://repo.pinokew.buzz/bitstreams/bitstream-uuid/download"
        ),
    )

    assert ok is True
    updated = client._parse_marc(captured["data"])
    fields_856 = updated.get_fields("856")
    assert len(fields_856) == 2
    assert fields_856[0]["u"] == "https://repo.pinokew.buzz/bitstreams/bitstream-uuid/download"
    assert fields_856[0]["y"] == "Файл"
    assert fields_856[1]["u"] == "https://repo.pinokew.buzz/handle/123/456"
    assert fields_856[1]["y"] == "Запис в репозиторії"


def test_dspace_create_item_error_is_diagnostic(monkeypatch):
    client = DSpaceClient()

    def fake_request(method, endpoint, **kwargs):
        return _Resp(
            status_code=500,
            payload={
                "message": (
                    "bad_dublin_core schema=koha.biblionumber.null. "
                    "Metadata field does not exist!"
                )
            },
        )

    monkeypatch.setattr(client, "_request", fake_request)

    try:
        client.create_item_direct("collection-uuid", {"dc.title": "Test"})
    except DSpaceRestError as exc:
        msg = str(exc)
    else:
        raise AssertionError("DSpaceRestError was not raised")

    assert "DSpace create item failed" in msg
    assert "HTTP 500" in msg
    assert "bad_dublin_core schema=koha.biblionumber.null" in msg
    assert "/core/items" in msg


def test_koha_step1_temp_upload_contract_headers_and_file_field(monkeypatch, tmp_path):
    client = KohaClient()
    captured = {}

    img = tmp_path / "cover.jpg"
    img.write_bytes(b"jpeg-bytes")

    def fake_post(url, files=None, headers=None, timeout=None):
        captured["url"] = url
        captured["files"] = files
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Resp(status_code=200, payload={"fileid": "tmp-file-id"})

    monkeypatch.setattr(client.cgi_session, "post", fake_post)

    temp_id = client._step1_upload_temp(str(img), "csrf-123", "http://koha/tool")

    assert temp_id == "tmp-file-id"
    assert captured["url"].endswith("/cgi-bin/koha/tools/upload-file.pl?temp=1")
    assert captured["headers"]["Referer"] == "http://koha/tool"
    assert captured["headers"]["CSRF-TOKEN"] == "csrf-123"
    assert captured["headers"]["X-Requested-With"] == "XMLHttpRequest"

    upload_field = captured["files"]["file"]
    assert upload_field[0] == "cover.jpg"
    assert upload_field[2] == "image/jpeg"


def test_koha_step2_attach_contract_payload_fields(monkeypatch):
    client = KohaClient()
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Resp(status_code=200, url="http://koha/cgi-bin/koha/mainpage.pl")

    monkeypatch.setattr(client.cgi_session, "post", fake_post)

    ok = client._step2_process_attach(42, "tmp-id", "csrf-xyz", "http://koha/tool")

    assert ok is True
    assert captured["data"] == {
        "biblionumber": "42",
        "filetype": "image",
        "op": "cud-process",
        "uploadedfileid": "tmp-id",
        "replace": "1",
        "csrf_token": "csrf-xyz",
    }
    assert captured["headers"]["Referer"] == "http://koha/tool"


def test_koha_cgi_login_contract_payload_field_names(monkeypatch):
    client = KohaClient()
    captured = {}

    login_page_html = '<form action="/cgi-bin/koha/svc/auth"></form>'

    def fake_get(url, timeout=None):
        return _Resp(status_code=200, text=login_page_html, url=url)

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return _Resp(status_code=200, text="Вихід", url="http://koha/cgi-bin/koha/mainpage.pl")

    monkeypatch.setattr(client.cgi_session, "get", fake_get)
    monkeypatch.setattr(client.cgi_session, "post", fake_post)
    monkeypatch.setattr(client, "_extract_csrf", lambda _html: "csrf-login")

    ok = client._ensure_cgi_login()

    assert ok is True
    assert captured["url"].endswith("/cgi-bin/koha/svc/auth")
    assert captured["headers"]["Referer"].endswith("/cgi-bin/koha/mainpage.pl")
    assert captured["data"]["csrf_token"] == "csrf-login"
    assert captured["data"]["op"] == "cud-login"
    assert captured["data"]["koha_login_context"] == "intranet"
    assert captured["data"]["login_userid"] == koha_module.KOHA_USER
    assert captured["data"]["login_password"] == koha_module.KOHA_PASS
    assert captured["data"]["branch"] == ""
