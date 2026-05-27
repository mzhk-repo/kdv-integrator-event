import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.export_module.db.repository import ExportRepository  # noqa: E402
from src.export_module.koha.client import KohaApiClientError  # noqa: E402
from src.export_module.koha.filters import filter_exportable_biblios  # noqa: E402


def _repo(tmp_path):
    return ExportRepository(str(tmp_path / "export_state.db"))


def _biblios(*biblionumbers):
    return [{"biblionumber": biblionumber} for biblionumber in biblionumbers]


def _candidate_ids(candidates):
    return [record["biblionumber"] for record in candidates]


def test_completed_biblionumber_is_excluded(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-completed")
    repo.mark_xlsx_generated("run-completed", "export.xlsx")
    repo.mark_gdrive_uploaded("run-completed", "/mnt/drive/export.xlsx", "/mnt/drive")
    repo.mark_email_sent("run-completed", "message-1")
    repo.mark_completed("run-completed")

    candidates = filter_exportable_biblios(
        _biblios(100, 101, 102), repo, max_retries=3
    )

    assert _candidate_ids(candidates) == [100, 102]


def test_retry_eligible_biblionumber_is_included(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-failed")
    repo.mark_failed("run-failed", "koha timeout")

    candidates = filter_exportable_biblios(_biblios(101), repo, max_retries=3)

    assert _candidate_ids(candidates) == [101]


def test_failed_biblionumber_over_retry_limit_is_excluded(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-failed")
    repo.mark_failed("run-failed", "koha timeout")
    repo.mark_failed("run-failed", "koha timeout again")

    candidates = filter_exportable_biblios(
        _biblios(101, 102), repo, max_retries=2
    )

    assert _candidate_ids(candidates) == [102]


def test_recoverable_staged_runs_do_not_create_duplicate_export(tmp_path):
    repo = _repo(tmp_path)
    repo.insert_pending([101], "run-xlsx")
    repo.insert_pending([102], "run-drive")
    repo.insert_pending([103], "run-email")
    repo.mark_xlsx_generated("run-xlsx", "xlsx.xlsx")
    repo.mark_xlsx_generated("run-drive", "drive.xlsx")
    repo.mark_gdrive_uploaded("run-drive", "/mnt/drive/drive.xlsx", "/mnt/drive")
    repo.mark_xlsx_generated("run-email", "email.xlsx")
    repo.mark_gdrive_uploaded("run-email", "/mnt/drive/email.xlsx", "/mnt/drive")
    repo.mark_email_sent("run-email", "message-1")

    candidates = filter_exportable_biblios(
        _biblios(100, 101, 102, 103, 104),
        repo,
        max_retries=3,
    )

    assert _candidate_ids(candidates) == [100, 104]


def test_runtime_range_excludes_records_outside_requested_bounds(tmp_path):
    repo = _repo(tmp_path)

    candidates = filter_exportable_biblios(
        _biblios(99, 100, 101, 102, 103),
        repo,
        max_retries=3,
        biblionumber_from=100,
        biblionumber_to=102,
    )

    assert _candidate_ids(candidates) == [100, 101, 102]


@pytest.mark.parametrize(
    "biblionumber_from,biblionumber_to",
    [
        (0, None),
        (None, 0),
        (-1, 10),
        (20, 10),
    ],
)
def test_invalid_runtime_range_is_rejected(
    tmp_path, biblionumber_from, biblionumber_to
):
    repo = _repo(tmp_path)

    with pytest.raises(ValueError, match="biblionumber"):
        filter_exportable_biblios(
            _biblios(101),
            repo,
            max_retries=3,
            biblionumber_from=biblionumber_from,
            biblionumber_to=biblionumber_to,
        )


def test_invalid_biblionumber_payload_is_rejected(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(KohaApiClientError, match="biblionumber"):
        filter_exportable_biblios([{"title": "missing id"}], repo, max_retries=3)
