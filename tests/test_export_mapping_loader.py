import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.marc.mapping_loader import (  # noqa: E402
    MappingLoader,
    MappingValidationError,
)


def _write_valid_files(tmp_path):
    mapping = tmp_path / "marc_mapping.yaml"
    dictionaries = tmp_path / "export_dictionaries.yaml"
    mapping.write_text(
        """
version: 1
columns:
  - name: "ID Запису"
    sources:
      - field: "001"
  - name: "Тип документа"
    sources:
      - field: "942"
        subfields: ["c"]
        transform: "authorized_value"
        dictionary: "itemtypes"
static_columns:
  - name: "Бібліотека-отримувач"
    value: "REDACTED_LIBRARY_NAME"
    reason: "Потрібно для downstream import"
required_columns:
  - "ID Запису"
  - "Тип документа"
  - "Бібліотека-отримувач"
""".lstrip(),
        encoding="utf-8",
    )
    dictionaries.write_text(
        """
version: 1
authorized_values:
  itemtypes:
    BOOK: "Книга"
    BK: "Книга"
unknown_policy:
  authorized_value: "keep_code"
""".lstrip(),
        encoding="utf-8",
    )
    return mapping, dictionaries


def test_static_columns_are_loaded(tmp_path):
    mapping_path, dictionaries_path = _write_valid_files(tmp_path)

    mapping = MappingLoader(mapping_path, dictionaries_path).load()

    assert [column.name for column in mapping.static_columns] == [
        "Бібліотека-отримувач"
    ]
    assert mapping.static_columns[0].value == "REDACTED_LIBRARY_NAME"
    assert mapping.column_names == [
        "ID Запису",
        "Тип документа",
        "Бібліотека-отримувач",
    ]


def test_authorized_value_dictionary_maps_code_to_label(tmp_path):
    mapping_path, dictionaries_path = _write_valid_files(tmp_path)

    mapping = MappingLoader(mapping_path, dictionaries_path).load()

    assert mapping.dictionaries.apply_authorized_value("itemtypes", "BOOK") == "Книга"
    assert (
        mapping.dictionaries.apply_authorized_value("itemtypes", "UNKNOWN")
        == "UNKNOWN"
    )


def test_unknown_dictionary_id_raises(tmp_path):
    mapping_path, dictionaries_path = _write_valid_files(tmp_path)
    mapping_path.write_text(
        """
version: 1
columns:
  - name: "Тип документа"
    sources:
      - field: "942"
        subfields: ["c"]
        transform: "authorized_value"
        dictionary: "missing"
required_columns:
  - "Тип документа"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(MappingValidationError, match="missing"):
        MappingLoader(mapping_path, dictionaries_path).load()


def test_required_columns_must_exist(tmp_path):
    mapping_path, dictionaries_path = _write_valid_files(tmp_path)
    mapping_path.write_text(
        """
version: 1
columns:
  - name: "ID Запису"
    sources:
      - field: "001"
static_columns: []
required_columns:
  - "Неіснуюча колонка"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(MappingValidationError, match="required_columns"):
        MappingLoader(mapping_path, dictionaries_path).load()
