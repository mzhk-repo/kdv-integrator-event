"""Завантаження і валідація декларативного MARC -> XLSX mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class MappingValidationError(ValueError):
    """Помилка валідації mapping або export dictionaries."""


@dataclass(frozen=True)
class SourceMapping:
    field: str
    subfields: list[str] = field(default_factory=list)
    join: str = " "
    strip_chars: str = ""
    transform: str = ""
    dictionary: str = ""


@dataclass(frozen=True)
class ColumnMapping:
    name: str
    sources: list[SourceMapping]


@dataclass(frozen=True)
class StaticColumn:
    name: str
    value: str
    reason: str = ""


@dataclass(frozen=True)
class AuthorizedValueDictionary:
    name: str
    values: dict[str, str]


@dataclass(frozen=True)
class ExportDictionaries:
    authorized_values: dict[str, AuthorizedValueDictionary]
    unknown_policy: dict[str, str]

    def apply_authorized_value(
        self, dictionary_id: str, raw_code: str | None
    ) -> str | None:
        if raw_code is None:
            return None

        dictionary = self.authorized_values.get(dictionary_id)
        if dictionary is None:
            raise MappingValidationError(
                f"Unknown authorized value dictionary: {dictionary_id}"
            )

        mapped_value = dictionary.values.get(raw_code)
        if mapped_value is not None:
            return mapped_value

        policy = self.unknown_policy.get("authorized_value", "keep_code")
        if policy == "keep_code":
            return raw_code
        if policy == "empty":
            return ""
        if policy == "fail":
            raise MappingValidationError(
                f"Unknown authorized value code '{raw_code}' "
                f"for dictionary: {dictionary_id}"
            )
        raise MappingValidationError(
            f"Unsupported authorized_value unknown_policy: {policy}"
        )


@dataclass(frozen=True)
class MARCMapping:
    columns: list[ColumnMapping]
    static_columns: list[StaticColumn]
    required_columns: list[str]
    dictionaries: ExportDictionaries

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns] + [
            column.name for column in self.static_columns
        ]


class MappingLoader:
    def __init__(self, mapping_path: str | Path, dictionaries_path: str | Path) -> None:
        self.mapping_path = Path(mapping_path)
        self.dictionaries_path = Path(dictionaries_path)

    def load(self) -> MARCMapping:
        mapping_payload = _load_yaml_mapping(self.mapping_path)
        dictionaries = self._load_dictionaries()

        columns = _parse_columns(mapping_payload.get("columns", []))
        static_columns = _parse_static_columns(mapping_payload.get("static_columns", []))
        required_columns = _parse_string_list(
            mapping_payload.get("required_columns", []), "required_columns"
        )

        marc_mapping = MARCMapping(
            columns=columns,
            static_columns=static_columns,
            required_columns=required_columns,
            dictionaries=dictionaries,
        )
        _validate_mapping(marc_mapping)
        return marc_mapping

    def _load_dictionaries(self) -> ExportDictionaries:
        payload = _load_yaml_mapping(self.dictionaries_path)
        authorized_values_payload = payload.get("authorized_values", {})
        if not isinstance(authorized_values_payload, dict):
            raise MappingValidationError("authorized_values must be a mapping")

        authorized_values: dict[str, AuthorizedValueDictionary] = {}
        for name, values in authorized_values_payload.items():
            if not isinstance(name, str) or not name:
                raise MappingValidationError(
                    "authorized_values dictionary name must be a string"
                )
            if not isinstance(values, dict):
                raise MappingValidationError(
                    f"authorized_values.{name} must be a mapping"
                )
            authorized_values[name] = AuthorizedValueDictionary(
                name=name,
                values={str(code): str(label) for code, label in values.items()},
            )

        unknown_policy = payload.get("unknown_policy", {})
        if unknown_policy is None:
            unknown_policy = {}
        if not isinstance(unknown_policy, dict):
            raise MappingValidationError("unknown_policy must be a mapping")

        normalized_policy = {str(key): str(value) for key, value in unknown_policy.items()}
        policy = normalized_policy.get("authorized_value", "keep_code")
        if policy not in {"keep_code", "empty", "fail"}:
            raise MappingValidationError(
                "unknown_policy.authorized_value must be one of: "
                "keep_code, empty, fail"
            )

        return ExportDictionaries(
            authorized_values=authorized_values,
            unknown_policy=normalized_policy,
        )


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except FileNotFoundError as exc:
        raise MappingValidationError(f"Mapping file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise MappingValidationError(f"Invalid YAML file: {path}") from exc

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MappingValidationError(f"YAML root must be a mapping: {path}")
    return payload


def _parse_columns(payload: Any) -> list[ColumnMapping]:
    if not isinstance(payload, list):
        raise MappingValidationError("columns must be a list")

    columns: list[ColumnMapping] = []
    for item in payload:
        if not isinstance(item, dict):
            raise MappingValidationError("Each column must be a mapping")
        name = _required_string(item, "name", "column")
        sources = _parse_sources(item.get("sources", []), name)
        if not sources:
            raise MappingValidationError(
                f"Column '{name}' must define at least one source"
            )
        columns.append(ColumnMapping(name=name, sources=sources))
    return columns


def _parse_sources(payload: Any, column_name: str) -> list[SourceMapping]:
    if not isinstance(payload, list):
        raise MappingValidationError(f"Column '{column_name}' sources must be a list")

    sources: list[SourceMapping] = []
    for item in payload:
        if not isinstance(item, dict):
            raise MappingValidationError(
                f"Column '{column_name}' source must be a mapping"
            )
        sources.append(
            SourceMapping(
                field=_required_string(item, "field", f"column '{column_name}' source"),
                subfields=_parse_string_list(item.get("subfields", []), "subfields"),
                join=str(item.get("join", " ")),
                strip_chars=str(item.get("strip_chars", "")),
                transform=str(item.get("transform", "")),
                dictionary=str(item.get("dictionary", "")),
            )
        )
    return sources


def _parse_static_columns(payload: Any) -> list[StaticColumn]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise MappingValidationError("static_columns must be a list")

    static_columns: list[StaticColumn] = []
    for item in payload:
        if not isinstance(item, dict):
            raise MappingValidationError("Each static column must be a mapping")
        static_columns.append(
            StaticColumn(
                name=_required_string(item, "name", "static column"),
                value=str(item.get("value", "")),
                reason=str(item.get("reason", "")),
            )
        )
    return static_columns


def _parse_string_list(payload: Any, field_name: str) -> list[str]:
    if payload is None:
        return []
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise MappingValidationError(f"{field_name} must be a list of strings")
    return payload


def _required_string(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MappingValidationError(f"{context} must define non-empty string '{key}'")
    return value


def _validate_mapping(mapping: MARCMapping) -> None:
    known_column_names = set(mapping.column_names)
    missing_required = [
        name for name in mapping.required_columns if name not in known_column_names
    ]
    if missing_required:
        raise MappingValidationError(
            "required_columns contains unknown columns: " + ", ".join(missing_required)
        )

    known_dictionaries = set(mapping.dictionaries.authorized_values)
    for column in mapping.columns:
        for source in column.sources:
            if source.transform == "authorized_value":
                if not source.dictionary:
                    raise MappingValidationError(
                        f"Column '{column.name}' authorized_value source must define "
                        "dictionary"
                    )
                if source.dictionary not in known_dictionaries:
                    raise MappingValidationError(
                        f"Unknown authorized value dictionary: {source.dictionary}"
                    )
