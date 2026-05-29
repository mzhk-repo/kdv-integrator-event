import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.koha.client import KohaApiClient  # noqa: E402


class _Response:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.auth = None

    def get(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("unexpected GET call")
        return self.responses.pop(0)


def _biblios(start, count):
    return [{"biblionumber": start + index} for index in range(count)]


def test_fetch_all_biblios_keyset_iterates_three_pages_of_ten_records():
    session = _Session(
        [
            _Response(_biblios(1, 10)),
            _Response(_biblios(11, 10)),
            _Response(_biblios(21, 10)),
            _Response([]),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=10, session=session
    )

    records = list(client.fetch_all_biblios_keyset())

    assert len(records) == 30
    assert records[0]["biblionumber"] == 1
    assert records[-1]["biblionumber"] == 30


def test_keyset_last_seen_id_uses_max_biblionumber_from_current_batch():
    session = _Session(
        [
            _Response(
                [
                    {"biblionumber": 3},
                    {"biblionumber": 1},
                    {"biblionumber": 7},
                ]
            ),
            _Response([]),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=3, session=session
    )

    assert [item["biblionumber"] for item in client.fetch_all_biblios_keyset()] == [
        3,
        1,
        7,
    ]
    assert session.calls[1]["kwargs"]["params"]["biblionumber"] == {">": 7}


def test_keyset_contract_uses_expected_koha_filter_params():
    session = _Session([_Response([])])
    client = KohaApiClient(
        "https://koha.example.org/", "user", "pass", page_size=25, session=session
    )

    assert list(client.fetch_all_biblios_keyset()) == []

    call = session.calls[0]
    assert call["url"] == "https://koha.example.org/api/v1/biblios"
    assert call["kwargs"]["params"] == {
        "_per_page": 25,
        "_order_by": "biblionumber",
        "biblionumber": {">": 0},
    }


def test_keyset_falls_back_to_page_when_koha_rejects_query_params():
    session = _Session(
        [
            _Response([{"message": "Malformed query string"}], status_code=400),
            _Response([{"biblio_id": 1}, {"biblio_id": 2}]),
            _Response([]),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=2, session=session
    )

    assert [item["biblio_id"] for item in client.fetch_all_biblios_keyset()] == [1, 2]

    assert session.calls[0]["kwargs"]["params"] == {
        "_per_page": 2,
        "_order_by": "biblionumber",
        "biblionumber": {">": 0},
    }
    assert session.calls[1]["kwargs"]["params"] == {"_per_page": 2, "_page": 1}
    assert session.calls[2]["kwargs"]["params"] == {"_per_page": 2, "_page": 2}


def test_keyset_fallback_is_not_used_for_unrelated_koha_errors():
    session = _Session([_Response({"error": "unauthorized"}, status_code=401)])
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=2, session=session
    )

    with pytest.raises(Exception, match="HTTP 401"):
        list(client.fetch_all_biblios_keyset())

    assert len(session.calls) == 1


def test_offset_fallback_uses_koha_page_pagination():
    session = _Session(
        [
            _Response([{"biblio_id": 1}, {"biblio_id": 2}, {"biblio_id": 3}]),
            _Response([{"biblio_id": 4}, {"biblio_id": 5}]),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=3, session=session
    )

    assert [item["biblio_id"] for item in client.fetch_all_biblios_offset_fallback()] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert session.calls[0]["kwargs"]["params"] == {"_per_page": 3, "_page": 1}
    assert session.calls[1]["kwargs"]["params"] == {"_per_page": 3, "_page": 2}


def test_fetch_biblio_marcxml_uses_marcxml_accept_header():
    session = _Session([_Response(text="<record />")])
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=10, session=session
    )

    marcxml = client.fetch_biblio_marcxml(42)

    assert marcxml == "<record />"
    assert session.calls[0]["url"] == "https://koha.example.org/api/v1/biblios/42"
    assert session.calls[0]["kwargs"]["headers"] == {
        "Accept": "application/marcxml+xml"
    }


def test_keyset_range_returns_only_requested_biblionumbers():
    session = _Session(
        [
            _Response(_biblios(1000, 10)),
            _Response(_biblios(1010, 10)),
            _Response(_biblios(1020, 10)),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=10, session=session
    )

    records = list(
        client.fetch_all_biblios_keyset(
            biblionumber_from=1005,
            biblionumber_to=1012,
        )
    )

    assert [item["biblionumber"] for item in records] == list(range(1005, 1013))


def test_keyset_range_starts_from_lower_bound_minus_one():
    session = _Session([_Response([])])
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=10, session=session
    )

    assert list(client.fetch_all_biblios_keyset(biblionumber_from=1000)) == []

    assert session.calls[0]["kwargs"]["params"]["biblionumber"] == {">": 999}


def test_offset_fallback_filters_requested_biblionumber_range():
    session = _Session(
        [
            _Response(_biblios(1, 5)),
            _Response(_biblios(6, 5)),
            _Response(_biblios(11, 5)),
        ]
    )
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=5, session=session
    )

    records = list(
        client.fetch_all_biblios_offset_fallback(
            biblionumber_from=4,
            biblionumber_to=11,
        )
    )

    assert [item["biblionumber"] for item in records] == list(range(4, 12))


@pytest.mark.parametrize(
    "biblionumber_from,biblionumber_to",
    [
        (0, None),
        (None, 0),
        (-1, 10),
        (20, 10),
    ],
)
def test_invalid_biblionumber_range_is_rejected(
    biblionumber_from, biblionumber_to
):
    client = KohaApiClient(
        "https://koha.example.org", "user", "pass", page_size=10, session=_Session([])
    )

    with pytest.raises(ValueError, match="biblionumber"):
        list(
            client.fetch_all_biblios_keyset(
                biblionumber_from=biblionumber_from,
                biblionumber_to=biblionumber_to,
            )
        )


def test_page_size_rejects_unbounded_export_request():
    with pytest.raises(ValueError, match="page_size"):
        KohaApiClient("https://koha.example.org", "user", "pass", page_size=99999)
