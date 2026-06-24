"""Захисний MARCXML parser для Koha export."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from src.export_module.marc.mapping_loader import (
    MARCMapping,
    MappingValidationError,
    SourceMapping,
)

LOGGER = logging.getLogger(__name__)


class MARCParser:
    def __init__(self, mapping: MARCMapping) -> None:
        self.mapping = mapping

    def parse_record(self, marcxml: str) -> dict[str, str | None] | None:
        record = _parse_marc_record(marcxml)
        if record is None:
            return None

        parsed: dict[str, str | None] = {}
        for column in self.mapping.columns:
            parsed[column.name] = self._extract_column(record, column)

        for static_column in self.mapping.static_columns:
            parsed[static_column.name] = static_column.value

        return parsed

    def apply_authorized_value(
        self, dictionary_id: str, raw_code: str | None
    ) -> str | None:
        return self.mapping.dictionaries.apply_authorized_value(dictionary_id, raw_code)

    def has_file_link(self, marcxml: str) -> bool:
        record = _parse_marc_record(marcxml)
        if record is None:
            return False
        return _has_856_file_link(record)

    def _extract_column(self, record: ET.Element, column) -> str | None:
        values: list[str] = []
        for source in column.sources:
            value = self._extract_source(record, source)
            if value is not None:
                values.append(value)

        if not values:
            return None
        value = " ".join(values)
        if column.transform == "extract_year_regex":
            return _extract_year_regex(value)
        if column.transform:
            raise MappingValidationError(f"Unsupported transform: {column.transform}")
        return value

    def _extract_source(
        self, record: ET.Element, source: SourceMapping
    ) -> str | None:
        if source.subfields:
            value = _extract_data_field(record, source)
        else:
            value = _extract_control_field(record, source.field)
            if value is None:
                value = _extract_data_field(record, source)

        if value is None:
            return None

        value = value.strip(source.strip_chars).strip()
        if value == "":
            return None

        if source.transform == "authorized_value":
            if not source.dictionary:
                raise MappingValidationError(
                    "authorized_value source must define dictionary"
                )
            return self.apply_authorized_value(source.dictionary, value)

        if source.transform == "extract_year_regex":
            return _extract_year_regex(value)

        if source.transform:
            raise MappingValidationError(f"Unsupported transform: {source.transform}")

        return value


def _parse_marc_record(marcxml: str) -> ET.Element | None:
    try:
        root = ET.fromstring(marcxml)
    except ET.ParseError as exc:
        LOGGER.warning("marc_parse_failed", extra={"error": str(exc)})
        return None

    record = _find_record(root)
    if record is None:
        LOGGER.warning(
            "marc_parse_failed", extra={"error": "record element not found"}
        )
    return record


def _has_856_file_link(record: ET.Element) -> bool:
    for datafield in record:
        if _local_name(datafield.tag) != "datafield":
            continue
        if datafield.attrib.get("tag") != "856":
            continue
        has_file_label = False
        has_url = False
        for subfield in datafield:
            if _local_name(subfield.tag) != "subfield":
                continue
            value = (subfield.text or "").strip()
            if subfield.attrib.get("code") == "y" and value == "Файл":
                has_file_label = True
            if subfield.attrib.get("code") == "u" and value:
                has_url = True
        if has_file_label and has_url:
            return True
    return False


def _find_record(root: ET.Element) -> ET.Element | None:
    if _local_name(root.tag) == "record":
        return root
    for child in root.iter():
        if _local_name(child.tag) == "record":
            return child
    return None


def _extract_control_field(record: ET.Element, tag: str) -> str | None:
    for field in record:
        if _local_name(field.tag) == "controlfield" and field.attrib.get("tag") == tag:
            text = field.text.strip() if field.text else ""
            return text or None
    return None


def _extract_data_field(record: ET.Element, source: SourceMapping) -> str | None:
    field_values: list[str] = []
    for datafield in record:
        if _local_name(datafield.tag) != "datafield":
            continue
        if datafield.attrib.get("tag") != source.field:
            continue
        if not _matches_condition(datafield, source):
            continue

        if source.subfields:
            subfield_values = [
                (subfield.text or "").strip()
                for subfield in datafield
                if _local_name(subfield.tag) == "subfield"
                and subfield.attrib.get("code") in source.subfields
                and (subfield.text or "").strip()
            ]
            if subfield_values:
                field_values.append(source.join.join(subfield_values))
        else:
            text = " ".join(
                (subfield.text or "").strip()
                for subfield in datafield
                if _local_name(subfield.tag) == "subfield"
                and (subfield.text or "").strip()
            )
            if text:
                field_values.append(text)

    if not field_values:
        return None
    return source.join.join(field_values)


def _matches_condition(datafield: ET.Element, source: SourceMapping) -> bool:
    if source.condition is None:
        return True
    for subfield in datafield:
        if _local_name(subfield.tag) != "subfield":
            continue
        if subfield.attrib.get("code") != source.condition.subfield:
            continue
        if (subfield.text or "").strip() == source.condition.equals:
            return True
    return False


def _extract_year_regex(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\d{4}", value)
    if not match:
        return None
    return match.group(0)


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag
