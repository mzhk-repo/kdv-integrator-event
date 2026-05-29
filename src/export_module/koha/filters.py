"""Фільтрація Koha biblios за state DB перед MARC parsing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.export_module.db.repository import ExportRepository
from src.export_module.koha.client import KohaApiClientError


def filter_exportable_biblios(
    biblios: Iterable[Mapping[str, Any]],
    repository: ExportRepository,
    max_retries: int,
    biblionumber_from: int | None = None,
    biblionumber_to: int | None = None,
) -> list[dict[str, Any]]:
    """Повертає Koha records, які можна запускати в новий export run."""
    _validate_biblionumber_range(biblionumber_from, biblionumber_to)

    completed = repository.get_completed_biblionumbers()
    failed = repository.get_failed_biblionumbers()
    retry_eligible = {
        record.biblionumber for record in repository.get_retry_eligible(max_retries)
    }
    recoverable = {
        record.biblionumber for record in repository.get_recoverable_runs()
    }

    candidates: list[dict[str, Any]] = []
    for biblio in biblios:
        biblionumber = _extract_biblionumber(biblio)
        if not _is_inside_range(biblionumber, biblionumber_from, biblionumber_to):
            continue
        if biblionumber in completed or biblionumber in recoverable:
            continue
        if biblionumber in failed and biblionumber not in retry_eligible:
            continue
        candidates.append(dict(biblio))

    return candidates


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
        raise ValueError(
            "biblionumber_from must be less than or equal to biblionumber_to"
        )


def _extract_biblionumber(item: Mapping[str, Any]) -> int:
    try:
        return int(item.get("biblionumber") or item["biblio_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KohaApiClientError("Koha biblio item has invalid biblionumber") from exc


def _is_inside_range(
    biblionumber: int,
    biblionumber_from: int | None,
    biblionumber_to: int | None,
) -> bool:
    if biblionumber_from is not None and biblionumber < biblionumber_from:
        return False
    if biblionumber_to is not None and biblionumber > biblionumber_to:
        return False
    return True
