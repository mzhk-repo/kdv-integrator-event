"""Koha REST API client для batch export."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_TIMEOUT_SECONDS = 30
MAX_PAGE_SIZE = 1000


class KohaApiClientError(RuntimeError):
    """Помилка Koha API client без витоку credentials."""


class KohaApiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        page_size: int = 100,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url is required")
        if page_size <= 0 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update({"Accept": "application/json"})

    def fetch_all_biblios_keyset(
        self,
        biblionumber_from: int | None = None,
        biblionumber_to: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        _validate_biblionumber_range(biblionumber_from, biblionumber_to)
        try:
            yield from self._fetch_all_biblios_keyset_only(
                biblionumber_from=biblionumber_from,
                biblionumber_to=biblionumber_to,
            )
        except KohaApiClientError as exc:
            if not _is_keyset_query_error(exc):
                raise
            yield from self.fetch_all_biblios_offset_fallback(
                biblionumber_from=biblionumber_from,
                biblionumber_to=biblionumber_to,
            )

    def _fetch_all_biblios_keyset_only(
        self,
        biblionumber_from: int | None = None,
        biblionumber_to: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        last_seen_id = biblionumber_from - 1 if biblionumber_from is not None else 0
        while True:
            batch = self._get_json_list(
                "/api/v1/biblios",
                params={
                    "_per_page": self.page_size,
                    "_order_by": "biblionumber",
                    "biblionumber": {">": last_seen_id},
                },
            )
            if not batch:
                break

            should_stop = False
            for biblio in batch:
                biblionumber = _extract_biblionumber(biblio)
                if biblionumber_to is not None and biblionumber > biblionumber_to:
                    should_stop = True
                    break
                if biblionumber_from is None or biblionumber >= biblionumber_from:
                    yield biblio

            last_seen_id = max(_extract_biblionumber(item) for item in batch)
            if should_stop or len(batch) < self.page_size:
                break

    def fetch_all_biblios_offset_fallback(
        self,
        biblionumber_from: int | None = None,
        biblionumber_to: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        _validate_biblionumber_range(biblionumber_from, biblionumber_to)
        page = 1
        while True:
            batch = self._get_json_list(
                "/api/v1/biblios",
                params={"_per_page": self.page_size, "_page": page},
            )
            if not batch:
                break

            should_stop = False
            for biblio in batch:
                biblionumber = _extract_biblionumber(biblio)
                if biblionumber_to is not None and biblionumber > biblionumber_to:
                    should_stop = True
                    break
                if biblionumber_from is None or biblionumber >= biblionumber_from:
                    yield biblio

            if should_stop or len(batch) < self.page_size:
                break
            page += 1

    def fetch_biblio_marcxml(self, biblionumber: int) -> str:
        response = self.session.get(
            self._url(f"/api/v1/biblios/{biblionumber}"),
            headers={"Accept": "application/marcxml+xml"},
            timeout=self.timeout,
        )
        self._raise_for_status(response, f"fetch biblio #{biblionumber} MARCXML")
        return response.text

    def _get_json_list(
        self, endpoint: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        response = self.session.get(
            self._url(endpoint), params=params, timeout=self.timeout
        )
        self._raise_for_status(response, f"GET {endpoint}")
        payload = response.json()
        if not isinstance(payload, list):
            raise KohaApiClientError(f"Koha {endpoint} returned non-list payload")
        for item in payload:
            if not isinstance(item, dict):
                raise KohaApiClientError(f"Koha {endpoint} returned non-object item")
        return payload

    def _raise_for_status(self, response: requests.Response, context: str) -> None:
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            detail = _response_error_detail(response)
            message = f"Koha API {context} failed: HTTP {status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise KohaApiClientError(message)

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"


def _validate_biblionumber_range(
    biblionumber_from: int | None, biblionumber_to: int | None
) -> None:
    for label, value in (
        ("biblionumber_from", biblionumber_from),
        ("biblionumber_to", biblionumber_to),
    ):
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise ValueError(f"{label} must be a positive integer")
    if (
        biblionumber_from is not None
        and biblionumber_to is not None
        and biblionumber_from > biblionumber_to
    ):
        raise ValueError("biblionumber_from must be less than or equal to biblionumber_to")


def _is_keyset_query_error(exc: KohaApiClientError) -> bool:
    message = str(exc)
    return "GET /api/v1/biblios failed: HTTP 400" in message or (
        "GET /api/v1/biblios failed: HTTP 500" in message
    )


def _response_error_detail(response: requests.Response) -> str:
    text = getattr(response, "text", "") or ""
    return " ".join(text.split())[:300]


def _extract_biblionumber(item: dict[str, Any]) -> int:
    try:
        return int(item.get("biblionumber") or item["biblio_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KohaApiClientError("Koha biblio item has invalid biblionumber") from exc
