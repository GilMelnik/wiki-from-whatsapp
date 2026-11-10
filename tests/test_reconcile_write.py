"""File-writing half of ``utils.reconcile``: reconciled writes, guarded writes, backups, locks.

The pure merge logic is covered in ``tests/test_reconcile.py``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from utils import reconcile
from utils.file_lock import file_lock
from utils.json_io import write_json_file
from utils.paths import Artifact
from utils.reconcile import (
    ReconcileReport,
    backup_file,
    guarded_write,
    load_previous,
    same_ignoring,
    write_reconciled,
)

LIST_SHAPED = {"items_key": "claims", "id_key": "claim_id"}
DICT_SHAPED = {"items_key": "pages", "id_key": None}
# A 0.1 s lock timeout must fail well before file_lock's own default would.
SHORT_TIMEOUT_BOUND = 5.0


class _FixedClock:
    @staticmethod
    def now() -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture(autouse=True)
def backups_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "backups"
    monkeypatch.setattr(reconcile, "BACKUPS_DIR", path)
    monkeypatch.setattr(reconcile, "datetime", _FixedClock)
    return path


@pytest.fixture
def artifact(tmp_path: Path) -> Artifact:
    return Artifact(tmp_path / "claims.json", tmp_path / "claims_edited.json")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _claim(claim_id: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"claim_id": claim_id, "text": text, **extra}


def _claims(run: int, *claims: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"run": run}, "claims": list(claims)}


def _changes(report: ReconcileReport) -> dict[str, tuple[str, ...]]:
    """The non-empty id lists of a report."""
    return {name: ids for name, ids in asdict(report.changes).items() if ids}


# ------------------------------------------------------------ write_reconciled


def test_without_edited_file_only_the_original_is_written(
    artifact: Artifact, backups_dir: Path
) -> None:
    first = _claims(1, _claim("a", "שלום"), _claim("b", "two"))
    second = _claims(2, _claim("a", "שלום, גרסה 2"), _claim("c", "three"))

    first_report = write_reconciled(artifact, first, **LIST_SHAPED)
    assert _read(artifact.original) == first
    second_report = write_reconciled(artifact, second, **LIST_SHAPED)

    assert _read(artifact.original) == second
    assert not artifact.edited.exists()
    assert not backups_dir.exists()
    assert first_report.backup is None
    assert _changes(first_report) == {"added": ("a", "b")}
    assert _changes(second_report) == {"added": ("c",), "updated": ("a",), "removed": ("b",)}


def test_rerun_preserves_manual_edits_and_refreshes_what_changed(
    artifact: Artifact, backups_dir: Path
) -> None:
    kept, deleted = _claim("kept", "one"), _claim("deleted", "two")
    changed, gone = _claim("changed", "three"), _claim("gone", "four")
    kept_by_hand = _claim("kept", "one, fixed by hand")
    mine = _claim("mine", "נוצר ידנית")
    edited_before = {
        "metadata": {"run": 1, "edited_by": "tool"},
        "claims": [kept_by_hand, _claim("changed", "three, fixed by hand"), gone, mine],
    }
    write_json_file(_claims(1, kept, deleted, changed, gone), artifact.original)
    write_json_file(edited_before, artifact.edited)
    changed_v2, new = _claim("changed", "three v2"), _claim("new", "five")
    payload = _claims(2, kept, deleted, changed_v2, new)

    report = write_reconciled(artifact, payload, **LIST_SHAPED)

    assert _read(artifact.original) == payload
    assert _read(artifact.edited) == _claims(2, kept_by_hand, changed_v2, new, mine)
    assert _changes(report) == {
        "added": ("new",),
        "updated": ("changed",),
        "removed": ("gone",),
        "superseded": ("changed",),
        "user_created": ("mine",),
    }
    assert report.backup == backups_dir / "claims_edited_20260102_030405.json"
    assert _read(report.backup) == edited_before
    assert [path.name for path in backups_dir.iterdir()] == [report.backup.name]


def test_untouched_edited_copy_stays_identical_to_the_original(artifact: Artifact) -> None:
    before = _claims(1, _claim("a", "one"), _claim("b", "two"))
    write_json_file(before, artifact.original)
    write_json_file(before, artifact.edited)
    payload = _claims(2, _claim("a", "one v2"), _claim("c", "three"))

    write_reconciled(artifact, payload, **LIST_SHAPED)

    assert _read(artifact.edited) == payload == _read(artifact.original)


def test_dict_shaped_items_are_merged_and_stay_a_dict(artifact: Artifact) -> None:
    before = {"pages": {"kept": {"body": "one"}, "changed": {"body": "two"}}}
    edited = {"pages": {"kept": {"body": "one, by hand"}, "mine": {"body": "שלום"}}}
    write_json_file(before, artifact.original)
    write_json_file(edited, artifact.edited)
    payload = {"pages": {"kept": {"body": "one"}, "changed": {"body": "two v2"}}}

    report = write_reconciled(artifact, payload, **DICT_SHAPED)

    assert _read(artifact.original) == payload
    assert _read(artifact.edited) == {
        "pages": {
            "kept": {"body": "one, by hand"},
            "changed": {"body": "two v2"},
            "mine": {"body": "שלום"},
        }
    }
    assert _changes(report) == {
        "updated": ("changed",),
        "resurrected": ("changed",),
        "user_created": ("mine",),
    }


def test_fresh_items_are_compared_as_json_stores_them(artifact: Artifact) -> None:
    write_json_file(_claims(1, _claim("a", "one", refs=["m1", "m2"])), artifact.original)
    write_json_file(_claims(1, _claim("a", "by hand", refs=["m1", "m2"])), artifact.edited)
    payload = _claims(2, _claim("a", "one", refs=("m1", "m2")))

    report = write_reconciled(artifact, payload, **LIST_SHAPED)

    assert _read(artifact.edited) == _claims(2, _claim("a", "by hand", refs=["m1", "m2"]))
    assert _changes(report) == {}


def test_unserialisable_payload_raises_before_anything_is_written(
    artifact: Artifact, backups_dir: Path
) -> None:
    before = _claims(1, _claim("a", "one"))
    write_json_file(before, artifact.original)
    write_json_file(before, artifact.edited)

    with pytest.raises(TypeError, match="not JSON serializable"):
        write_reconciled(artifact, _claims(2, _claim("a", object())), **LIST_SHAPED)

    assert _read(artifact.original) == before
    assert _read(artifact.edited) == before
    assert not backups_dir.exists()


def test_is_same_reaches_the_merge(artifact: Artifact) -> None:
    write_json_file(_claims(1, _claim("a", "one", at="monday")), artifact.original)
    write_json_file(_claims(1, _claim("a", "by hand", at="monday")), artifact.edited)
    payload = _claims(2, _claim("a", "one", at="tuesday"))

    report = write_reconciled(artifact, payload, **LIST_SHAPED, is_same=same_ignoring("at"))

    assert _read(artifact.edited) == _claims(2, _claim("a", "by hand", at="monday"))
    assert _changes(report) == {}


def test_rebuild_replaces_the_edited_file_after_backing_it_up(artifact: Artifact) -> None:
    edited_before = _claims(1, _claim("a", "by hand"), _claim("mine", "by hand"))
    write_json_file(_claims(1, _claim("a", "one")), artifact.original)
    write_json_file(edited_before, artifact.edited)
    payload = _claims(2, _claim("a", "one"))

    report = write_reconciled(artifact, payload, **LIST_SHAPED, rebuild=True)

    assert _read(artifact.original) == payload
    assert _read(artifact.edited) == payload
    assert _read(report.backup) == edited_before
    assert _changes(report) == {"added": ("a",)}


def test_rebuild_replaces_files_it_could_not_have_merged(artifact: Artifact) -> None:
    artifact.original.write_text("[]", encoding="utf-8")
    artifact.edited.write_text('{"claims": [', encoding="utf-8")
    payload = _claims(2, _claim("a", "one"))

    report = write_reconciled(artifact, payload, **LIST_SHAPED, rebuild=True)

    assert _read(artifact.original) == payload
    assert _read(artifact.edited) == payload
    assert report.backup.read_text(encoding="utf-8") == '{"claims": ['


def test_rebuild_without_edited_file_creates_none(artifact: Artifact, backups_dir: Path) -> None:
    write_json_file(_claims(1, _claim("a", "one")), artifact.original)
    payload = _claims(2, _claim("a", "one"))

    report = write_reconciled(artifact, payload, **LIST_SHAPED, rebuild=True)

    assert _read(artifact.original) == payload
    assert not artifact.edited.exists()
    assert report.backup is None
    assert not backups_dir.exists()


def test_edited_file_without_original_keeps_what_the_step_does_not_produce(
    artifact: Artifact,
) -> None:
    write_json_file(_claims(1, _claim("a", "by hand"), _claim("mine", "x")), artifact.edited)
    payload = _claims(2, _claim("a", "one"))

    report = write_reconciled(artifact, payload, **LIST_SHAPED)

    assert _read(artifact.edited) == _claims(2, _claim("a", "one"), _claim("mine", "x"))
    assert _changes(report) == {
        "added": ("a",),
        "superseded": ("a",),
        "user_created": ("mine",),
    }


@pytest.mark.parametrize("held", ["original", "edited"])
def test_write_reconciled_times_out_while_either_file_is_locked(
    artifact: Artifact, backups_dir: Path, held: str
) -> None:
    before = _claims(1, _claim("a", "one"))
    write_json_file(before, artifact.original)
    write_json_file(before, artifact.edited)

    started = time.monotonic()
    with file_lock(getattr(artifact, held)):
        with pytest.raises(TimeoutError, match="could not lock"):
            write_reconciled(
                artifact, _claims(2, _claim("a", "two")), **LIST_SHAPED, lock_timeout=0.1
            )

    assert time.monotonic() - started < SHORT_TIMEOUT_BOUND
    assert _read(artifact.original) == before
    assert _read(artifact.edited) == before
    assert not backups_dir.exists()


def test_malformed_edited_file_raises_before_anything_is_written(
    artifact: Artifact, backups_dir: Path
) -> None:
    before = _claims(1, _claim("a", "one"))
    write_json_file(before, artifact.original)
    artifact.edited.write_text('{"claims": [', encoding="utf-8")

    with pytest.raises(ValueError, match="claims_edited.json is not valid JSON"):
        write_reconciled(artifact, _claims(2, _claim("a", "two")), **LIST_SHAPED)

    assert _read(artifact.original) == before
    assert not backups_dir.exists()


@pytest.mark.parametrize(
    ("edited", "message"),
    [
        ({"metadata": {}}, "claims_edited.json: 'claims' must be a list"),
        ({"claims": {"a": {}}}, "claims_edited.json: 'claims' must be a list"),
        ({"claims": [{"text": "no id"}]}, r"claims_edited.json: claims\[0\] has no string"),
        ({"claims": [_claim("a", "x"), _claim("a", "y")]}, "duplicate claim_id 'a'"),
    ],
)
def test_unusable_edited_items_raise_before_anything_is_written(
    artifact: Artifact, backups_dir: Path, edited: dict[str, Any], message: str
) -> None:
    before = _claims(1, _claim("a", "one"))
    write_json_file(before, artifact.original)
    write_json_file(edited, artifact.edited)

    with pytest.raises(ValueError, match=message):
        write_reconciled(artifact, _claims(2, _claim("a", "two")), **LIST_SHAPED)

    assert _read(artifact.original) == before
    assert _read(artifact.edited) == edited
    assert not backups_dir.exists()


@pytest.mark.parametrize(
    ("payload", "keys", "message"),
    [
        (_claims(1, _claim("a", "x"), _claim("a", "y")), LIST_SHAPED, "payload: duplicate"),
        ({"claims": [{"claim_id": 7}]}, LIST_SHAPED, r"payload: claims\[0\] has no string"),
        ({"pages": [{"id": "a"}]}, DICT_SHAPED, "payload: 'pages' must be a dict"),
        ({"metadata": {}}, DICT_SHAPED, "payload: 'pages' must be a dict"),
    ],
)
def test_unusable_payload_raises_and_writes_nothing(
    artifact: Artifact, payload: dict[str, Any], keys: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        write_reconciled(artifact, payload, **keys)

    assert not artifact.original.exists()


def test_retry_after_a_failure_between_the_two_writes_repeats_the_merge(
    artifact: Artifact, monkeypatch: pytest.MonkeyPatch
) -> None:
    a_by_hand = _claim("a", "one, by hand")
    write_json_file(_claims(1, _claim("a", "one"), _claim("b", "two")), artifact.original)
    write_json_file(_claims(1, a_by_hand, _claim("b", "two, by hand")), artifact.edited)
    payload = _claims(2, _claim("a", "one"), _claim("b", "two v2"), _claim("c", "three"))

    def fail_on_original(content: dict[str, Any], path: Path) -> None:
        if path == artifact.original:
            raise OSError("disk full")
        write_json_file(content, path)

    with monkeypatch.context() as patch:
        patch.setattr(reconcile, "write_json_file", fail_on_original)
        with pytest.raises(OSError, match="disk full"):
            write_reconciled(artifact, payload, **LIST_SHAPED)
    report = write_reconciled(artifact, payload, **LIST_SHAPED)

    assert _read(artifact.original) == payload
    assert _read(artifact.edited) == _claims(
        2, a_by_hand, _claim("b", "two v2"), _claim("c", "three")
    )
    assert _changes(report) == {"added": ("c",), "updated": ("b",)}


def test_write_reconciled_logs_one_summary_line_per_call(
    artifact: Artifact, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="utils.reconcile")
    write_json_file(_claims(1, _claim("a", "one")), artifact.original)
    write_json_file(_claims(1, _claim("a", "by hand")), artifact.edited)

    write_reconciled(artifact, _claims(1, _claim("a", "one")), **LIST_SHAPED)
    write_reconciled(artifact, _claims(2, _claim("a", "one v2")), **LIST_SHAPED)

    quiet, loud = caplog.records
    assert quiet.levelno == logging.INFO
    assert "reconciled claims.json: added=0, updated=0" in quiet.getMessage()
    assert loud.levelno == logging.WARNING
    assert "superseded=['a']" in loud.getMessage()
    assert "claims_edited_20260102_030405_1.json" in loud.getMessage()


# ----------------------------------- load_previous / guarded_write / backup_file


def test_load_previous_reads_the_original_even_when_an_edited_copy_exists(
    artifact: Artifact,
) -> None:
    assert load_previous(artifact) == {}

    write_json_file(_claims(1, _claim("a", "one")), artifact.original)
    write_json_file(_claims(1, _claim("a", "by hand")), artifact.edited)

    assert load_previous(artifact) == _claims(1, _claim("a", "one"))


def test_load_previous_rejects_a_file_that_is_not_a_json_object(artifact: Artifact) -> None:
    artifact.original.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(ValueError, match="claims.json must hold a JSON object, found list"):
        load_previous(artifact)


def test_guarded_write_with_backup_keeps_the_previous_content(
    tmp_path: Path, backups_dir: Path
) -> None:
    target = tmp_path / "plan.json"
    assert guarded_write(target, {"tree": ["old"]}, backup=True) is None

    backup = guarded_write(target, {"tree": ["new"]}, backup=True)

    assert _read(target) == {"tree": ["new"]}
    assert backup == backups_dir / "plan_20260102_030405.json"
    assert _read(backup) == {"tree": ["old"]}


def test_guarded_write_without_backup_makes_none(tmp_path: Path, backups_dir: Path) -> None:
    target = tmp_path / "plan.json"
    write_json_file({"tree": ["old"]}, target)

    assert guarded_write(target, {"tree": ["new"]}, backup=False) is None

    assert _read(target) == {"tree": ["new"]}
    assert not backups_dir.exists()


def test_guarded_write_failure_leaves_the_previous_file_and_no_partial_one(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plan.json"
    write_json_file({"tree": ["old"]}, target)

    with pytest.raises(TypeError):
        guarded_write(target, {"tree": object()}, backup=False)

    assert _read(target) == {"tree": ["old"]}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plan.json", "plan.json.lock"]


def test_guarded_write_times_out_while_the_file_is_locked(tmp_path: Path) -> None:
    target = tmp_path / "plan.json"

    started = time.monotonic()
    with file_lock(target):
        with pytest.raises(TimeoutError, match="could not lock"):
            guarded_write(target, {"tree": []}, backup=True, lock_timeout=0.1)

    assert time.monotonic() - started < SHORT_TIMEOUT_BOUND
    assert not target.exists()


def test_backup_file_never_overwrites_an_earlier_backup(
    tmp_path: Path, backups_dir: Path
) -> None:
    target = tmp_path / "claims_edited.json"
    assert backup_file(target) is None

    target.write_text("first", encoding="utf-8")
    first = backup_file(target)
    target.write_text("second", encoding="utf-8")
    second = backup_file(target)

    assert first == backups_dir / "claims_edited_20260102_030405.json"
    assert second == backups_dir / "claims_edited_20260102_030405_1.json"
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
