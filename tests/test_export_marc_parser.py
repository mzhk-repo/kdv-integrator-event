import logging
import sys
from dataclasses import replace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.marc.mapping_loader import (  # noqa: E402
    AuthorizedValueDictionary,
    ColumnMapping,
    ExportDictionaries,
    MARCMapping,
    SourceMapping,
    StaticColumn,
)
from src.export_module.marc.parser import MARCParser  # noqa: E402


def _mapping(unknown_policy="keep_code"):
    return MARCMapping(
        columns=[
            ColumnMapping(
                name="ID Запису",
                sources=[SourceMapping(field="001")],
            ),
            ColumnMapping(
                name="Назва книги",
                sources=[
                    SourceMapping(
                        field="245",
                        subfields=["a", "b"],
                        join=" ",
                        strip_chars=" /:",
                    )
                ],
            ),
            ColumnMapping(
                name="Тип документа",
                sources=[
                    SourceMapping(
                        field="942",
                        subfields=["c"],
                        transform="authorized_value",
                        dictionary="itemtypes",
                    )
                ],
            ),
        ],
        static_columns=[
            StaticColumn(
                name="Бібліотека-отримувач",
                value="REDACTED_LIBRARY_NAME",
                reason="Потрібно для downstream import",
            )
        ],
        required_columns=["ID Запису", "Назва книги", "Тип документа"],
        dictionaries=ExportDictionaries(
            authorized_values={
                "itemtypes": AuthorizedValueDictionary(
                    name="itemtypes",
                    values={"BOOK": "Книга"},
                )
            },
            unknown_policy={"authorized_value": unknown_policy},
        ),
    )


VALID_MARCXML = """
<record xmlns="http://www.loc.gov/MARC21/slim">
  <leader>00000nam a2200000 i 4500</leader>
  <controlfield tag="001">12345</controlfield>
  <datafield tag="245" ind1="1" ind2="0">
    <subfield code="a">Назва книги :</subfield>
    <subfield code="b">підзаголовок /</subfield>
  </datafield>
  <datafield tag="942" ind1=" " ind2=" ">
    <subfield code="c">BOOK</subfield>
  </datafield>
</record>
""".strip()


def test_missing_marc_fields_return_none_without_exception():
    parser = MARCParser(_mapping())
    marcxml = """
<record xmlns="http://www.loc.gov/MARC21/slim">
  <controlfield tag="001">12345</controlfield>
</record>
""".strip()

    parsed = parser.parse_record(marcxml)

    assert parsed == {
        "ID Запису": "12345",
        "Назва книги": None,
        "Тип документа": None,
        "Бібліотека-отримувач": "REDACTED_LIBRARY_NAME",
    }


def test_static_columns_are_added_after_marc_extraction():
    parser = MARCParser(_mapping())

    parsed = parser.parse_record(VALID_MARCXML)

    assert parsed is not None
    assert list(parsed) == [
        "ID Запису",
        "Назва книги",
        "Тип документа",
        "Бібліотека-отримувач",
    ]
    assert parsed["Бібліотека-отримувач"] == "REDACTED_LIBRARY_NAME"


def test_authorized_value_book_exports_as_cyrillic_label():
    parser = MARCParser(_mapping())

    parsed = parser.parse_record(VALID_MARCXML)

    assert parsed is not None
    assert parsed["ID Запису"] == "12345"
    assert parsed["Назва книги"] == "Назва книги : підзаголовок"
    assert parsed["Тип документа"] == "Книга"


def test_unknown_authorized_value_follows_keep_code_policy():
    mapping = _mapping(unknown_policy="keep_code")
    itemtypes = mapping.dictionaries.authorized_values["itemtypes"]
    mapping = replace(
        mapping,
        dictionaries=replace(
            mapping.dictionaries,
            authorized_values={
                "itemtypes": replace(itemtypes, values={"BK": "Книга"})
            },
        ),
    )
    parser = MARCParser(mapping)

    parsed = parser.parse_record(VALID_MARCXML)

    assert parsed is not None
    assert parsed["Тип документа"] == "BOOK"


def test_malformed_marcxml_logs_warning_and_returns_none(caplog):
    parser = MARCParser(_mapping())

    with caplog.at_level(logging.WARNING):
        parsed = parser.parse_record("<record><broken></record>")

    assert parsed is None
    assert "marc_parse_failed" in caplog.text


@pytest.mark.parametrize(
    "unknown_policy,expected",
    [
        ("empty", ""),
        ("keep_code", "UNKNOWN"),
    ],
)
def test_apply_authorized_value_uses_dictionary_policy(unknown_policy, expected):
    parser = MARCParser(_mapping(unknown_policy=unknown_policy))

    assert parser.apply_authorized_value("itemtypes", "UNKNOWN") == expected
    assert parser.apply_authorized_value("itemtypes", None) is None
