import os
import hashlib
import requests
import logging
import time
from .config import DSPACE_API_URL, DSPACE_USER, DSPACE_PASS, TIMEOUT, UPLOAD_TIMEOUT
from .mapping import strip_metadata_edges

logger = logging.getLogger("DSpaceClient")


class DSpaceRestError(RuntimeError):
    pass


class DSpaceClient:
    def __init__(self):
        self.base_url = DSPACE_API_URL
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.token = None

    def _update_xsrf_header(self):
        csrf_cookie = self.session.cookies.get("DSPACE-XSRF-COOKIE")
        if csrf_cookie:
            self.session.headers.update({"X-XSRF-TOKEN": csrf_cookie})

    def login(self) -> bool:
        auth_url = f"{self.base_url}/authn/login"
        try:
            self.session.get(f"{self.base_url}/authn/status", timeout=TIMEOUT)
            self._update_xsrf_header()
        except Exception as e:
            logger.error(f"⚠️ DSpace is unreachable: {e}")
            return False

        payload = {"user": DSPACE_USER, "password": DSPACE_PASS}
        try:
            resp = self.session.post(auth_url, data=payload, timeout=TIMEOUT)
            if resp.status_code in [200, 204]:
                self.token = resp.headers.get("Authorization")
                self.session.headers.update({"Authorization": self.token})
                self._update_xsrf_header()
                # logger.info(f"✅ DSpace Login Success ({DSPACE_USER})")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Login Exception: {e}")
            return False

    def _request(self, method, endpoint, **kwargs):
        if not self.token and endpoint != "/authn/login":
            if not self.login():
                return None

        url = f"{self.base_url}{endpoint}"
        current_timeout = kwargs.pop("timeout", TIMEOUT)

        try:
            resp = self.session.request(method, url, timeout=current_timeout, **kwargs)
            self._update_xsrf_header()

            if resp.status_code == 401:
                if self.login():
                    resp = self.session.request(
                        method, url, timeout=current_timeout, **kwargs
                    )
            return resp
        except Exception as e:
            logger.error(f"❌ Request Exception [{method} {endpoint}]: {e}")
            return None

    def find_item_uuid_by_handle(self, handle):
        endpoint = "/pid/find"
        resp = self._request("GET", endpoint, params={"id": handle})
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("uuid") and data.get("type") == "item":
                    return data.get("uuid")
            except Exception:
                pass
        return None

    def find_item_by_biblionumber(self, biblionumber):
        endpoint = "/discover/search/objects"
        query = f"koha.biblionumber:{biblionumber}"
        params = {"query": query, "dsoType": "item"}

        resp = self._request("GET", endpoint, params=params)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                results = (
                    data.get("_embedded", {})
                    .get("searchResult", {})
                    .get("_embedded", {})
                    .get("objects", [])
                )
                if results:
                    first_hit = results[0]["_embedded"]["indexableObject"]
                    return {
                        "uuid": first_hit["uuid"],
                        "handle": first_hit.get("handle"),
                    }
            except Exception:
                pass
        return None

    def find_item_by_record_uid(self, record_uid):
        """Find the DSpace item linked to a Koha MARC 001 UUID."""
        resp = self._request(
            "GET",
            "/discover/search/objects",
            params={"query": f"koha.uid:{record_uid}", "dsoType": "item", "size": 2},
        )
        if resp is None or resp.status_code != 200:
            self._raise_rest_error("DSpace find item by record UID", "/discover/search/objects", resp)
        try:
            search_results = resp.json().get("_embedded", {}).get("searchResults", {})
            page = search_results.get("page", {})
            if page.get("totalElements", 0) > 1:
                raise DSpaceRestError("Multiple DSpace items match record UID")
            hits = search_results.get("_embedded", {}).get("objects", [])
            if not hits:
                return None
            item = hits[0].get("_embedded", {}).get("indexableObject", {})
            item_uuid = item.get("uuid")
            if not item_uuid:
                raise DSpaceRestError("DSpace record UID search result has no Item UUID")
            item_resp = self._request("GET", f"/core/items/{item_uuid}")
            if item_resp is None or item_resp.status_code != 200:
                self._raise_rest_error(
                    "DSpace verify record UID", f"/core/items/{item_uuid}", item_resp
                )
            values = item_resp.json().get("metadata", {}).get("koha.uid", [])
            if not any(value.get("value") == record_uid for value in values):
                return None
            return {"uuid": item_uuid, "handle": item.get("handle")}
        except DSpaceRestError:
            raise
        except (AttributeError, KeyError, TypeError, IndexError):
            raise DSpaceRestError("Invalid DSpace record UID search response") from None
        return None

    # 🟢 НОВИЙ МЕТОД
    def get_item_last_modified(self, item_uuid):
        """Повертає рядок lastModified (ISO 8601) для Item"""
        resp = self._request("GET", f"/core/items/{item_uuid}")
        if resp and resp.status_code == 200:
            return resp.json().get("lastModified")
        return None

    def get_item(self, item_uuid):
        endpoint = f"/core/items/{item_uuid}"
        resp = self._request("GET", endpoint)
        if resp is not None and resp.status_code == 200:
            return resp.json()
        self._raise_rest_error("DSpace get item", endpoint, resp)

    def _format_metadata_value(self, value):
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        return [
            {"value": strip_metadata_edges(str(v)), "language": None} for v in values
        ]

    def _response_reason(self, resp):
        try:
            data = resp.json()
            if isinstance(data, dict):
                for key in ("message", "detail", "error", "title"):
                    value = data.get(key)
                    if value:
                        return str(value)
        except Exception:
            pass

        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return "empty response body"
        return " ".join(text.split())[:500]

    def _raise_rest_error(self, action, endpoint, resp):
        if resp is None:
            msg = f"{action} failed: no response from DSpace [{endpoint}]"
        else:
            msg = (
                f"{action} failed: HTTP {resp.status_code} "
                f"({self._response_reason(resp)}) [{endpoint}]"
            )
        logger.error(msg)
        raise DSpaceRestError(msg)

    def update_metadata(self, item_uuid, metadata_dict):
        operations = []
        for key, value in metadata_dict.items():
            if key in ["handle", "uuid"] or value is None:
                continue
            dspace_values = self._format_metadata_value(value)
            operations.append(
                {"op": "replace", "path": f"/metadata/{key}", "value": dspace_values}
            )

        if not operations:
            return True

        headers = {"Content-Type": "application/json-patch+json"}
        resp = self._request(
            "PATCH", f"/core/items/{item_uuid}", json=operations, headers=headers
        )
        if resp is not None and resp.status_code == 200:
            return True
        self._raise_rest_error(
            "DSpace update item metadata", f"/core/items/{item_uuid}", resp
        )

    def create_item_direct(self, collection_uuid, metadata_dict):
        # ... (код створення без змін, для скорочення місця, він ідентичний v6.5) ...
        dspace_metadata = {}
        if "dc.date.issued" not in metadata_dict or not metadata_dict["dc.date.issued"]:
            metadata_dict["dc.date.issued"] = str(time.localtime().tm_year)
        if "dc.type" not in metadata_dict or not metadata_dict["dc.type"]:
            metadata_dict["dc.type"] = "Book"

        for key, value in metadata_dict.items():
            if key in ["handle", "uuid"] or value is None:
                continue
            dspace_metadata[key] = self._format_metadata_value(value)

        name_val = metadata_dict.get("dc.title", "Untitled")
        if isinstance(name_val, list):
            name_val = name_val[0]
        name_val = strip_metadata_edges(str(name_val))

        data = {
            "name": name_val,
            "metadata": dspace_metadata,
            "inArchive": True,
            "discoverable": True,
        }
        resp = self._request(
            "POST",
            "/core/items",
            params={"owningCollection": collection_uuid},
            json=data,
        )

        if resp is not None and resp.status_code in [200, 201]:
            return resp.json()
        self._raise_rest_error("DSpace create item", "/core/items", resp)

    def get_primary_bitstream(self, item_uuid):
        bundle_uuid = self._get_original_bundle_uuid(item_uuid)
        if not bundle_uuid:
            return None
        endpoint = f"/core/bundles/{bundle_uuid}/primaryBitstream"
        resp = self._request("GET", endpoint)
        if resp is not None and resp.status_code == 204:
            return None
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            return data if data and data.get("uuid") else None
        self._raise_rest_error("DSpace get primary bitstream", endpoint, resp)

    def _get_original_bundle_uuid(self, item_uuid):
        endpoint = f"/core/items/{item_uuid}/bundles"
        resp = self._request("GET", endpoint)
        if resp is None or resp.status_code != 200:
            self._raise_rest_error("DSpace list item bundles", endpoint, resp)
        return next((
            bundle.get("uuid") for bundle in
            resp.json().get("_embedded", {}).get("bundles", [])
            if bundle.get("name") == "ORIGINAL"
        ), None)

    def set_primary_bitstream(self, item_uuid, bitstream_uuid):
        bundle_uuid = self._get_original_bundle_uuid(item_uuid)
        if not bundle_uuid:
            raise DSpaceRestError("DSpace Item has no ORIGINAL bundle")
        endpoint = f"/core/bundles/{bundle_uuid}/primaryBitstream"
        bitstream_url = f"{self.base_url}/core/bitstreams/{bitstream_uuid}"
        resp = self._request(
            "PUT", endpoint, data=bitstream_url,
            headers={"Content-Type": "text/uri-list"},
        )
        if resp is not None and resp.status_code == 200:
            return True
        self._raise_rest_error("DSpace set primary bitstream", endpoint, resp)

    def get_bitstream(self, bitstream_uuid):
        endpoint = f"/core/bitstreams/{bitstream_uuid}"
        resp = self._request("GET", endpoint)
        if resp is not None and resp.status_code == 200:
            data = resp.json()
            if data.get("uuid") != bitstream_uuid:
                raise DSpaceRestError("DSpace returned a mismatched bitstream UUID")
            return data
        self._raise_rest_error("DSpace get bitstream", endpoint, resp)

    def verify_bitstream_upload(self, bitstream_uuid, file_path):
        bitstream = self.get_bitstream(bitstream_uuid)
        checksum = bitstream.get("checkSum") or {}
        algorithm = checksum.get("checkSumAlgorithm", "").lower().replace("-", "")
        try:
            digest = hashlib.new(algorithm)
        except (ValueError, TypeError):
            raise DSpaceRestError("DSpace returned an unsupported bitstream checksum") from None
        size = os.path.getsize(file_path)
        if bitstream.get("sizeBytes") != size:
            raise DSpaceRestError("Uploaded DSpace bitstream size does not match source")
        with open(file_path, "rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != checksum.get("value", "").lower():
            raise DSpaceRestError("Uploaded DSpace bitstream checksum does not match source")
        return True

    def delete_bitstream(self, bitstream_uuid):
        endpoint = f"/core/bitstreams/{bitstream_uuid}"
        resp = self._request("DELETE", endpoint)
        if resp is not None and resp.status_code in (200, 204, 404):
            return True
        self._raise_rest_error("DSpace delete old bitstream", endpoint, resp)

    def upload_to_item(self, item_uuid, file_path, upload_name=None):
        if not os.path.exists(file_path):
            return False

        filename = os.path.basename(upload_name or file_path)
        if not filename:
            filename = os.path.basename(file_path)

        bundle_uuid = None
        resp = self._request("GET", f"/core/items/{item_uuid}/bundles")
        if resp is not None:
            for b in resp.json().get("_embedded", {}).get("bundles", []):
                if b["name"] == "ORIGINAL":
                    bundle_uuid = b["uuid"]

        if not bundle_uuid:
            resp = self._request(
                "POST", f"/core/items/{item_uuid}/bundles", json={"name": "ORIGINAL"}
            )
            if resp and resp.status_code in [200, 201]:
                bundle_uuid = resp.json()["uuid"]
            else:
                self._raise_rest_error(
                    "DSpace create ORIGINAL bundle",
                    f"/core/items/{item_uuid}/bundles",
                    resp,
                )

        old_ct = self.session.headers.pop("Content-Type", None)
        try:
            with open(file_path, "rb") as f:
                files = {"file": (filename, f, "application/pdf")}
                resp = self._request(
                    "POST",
                    f"/core/bundles/{bundle_uuid}/bitstreams",
                    files=files,
                    timeout=UPLOAD_TIMEOUT,
                )
                if resp and resp.status_code in [200, 201]:
                    try:
                        return resp.json()
                    except Exception:
                        return True
                self._raise_rest_error(
                    "DSpace upload bitstream",
                    f"/core/bundles/{bundle_uuid}/bitstreams",
                    resp,
                )
        except DSpaceRestError:
            raise
        except Exception:
            return False
        finally:
            if old_ct:
                self.session.headers["Content-Type"] = old_ct
