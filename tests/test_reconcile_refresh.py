"""``refresh_keys`` of ``utils.reconcile``: keep a manual edit AND refresh the fields the step owns.

Items are claim-shaped: ``text`` is what the user may scrub, ``count`` / ``dates`` are owned by the
step. Every row of the (user x step) table has its own case; the privacy rows come first.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from utils import reconcile
from utils.json_io import write_json_file
from utils.paths import Artifact
from utils.reconcile import merge_edited, same_ignoring, write_reconciled

REFRESH = ("count", "dates")
SCRUBBED = "text with the name removed"


def _claim(text: str = "v1", count: int = 1, **extra: Any) -> dict[str, Any]:
    return {"text": text, "count": count, "dates": [count], **extra}


def _merge(
    base: dict[str, Any], edited: dict[str, Any], fresh: dict[str, Any], **options: Any
) -> reconcile.MergeResult:
    return merge_edited(base, edited, fresh, refresh_keys=REFRESH, **options)


def _non_empty(result: reconcile.MergeResult) -> dict[str, tuple[str, ...]]:
    return {name: ids for name, ids in asdict(result.changes).items() if ids}


# --- the privacy rows: a scrubbed text or a deleted item never comes back for a count change


def test_scrubbed_text_stays_when_only_refresh_keys_change() -> None:
    base, fresh = {"a": _claim("raw text", 1)}, {"a": _claim("raw text", 5)}
    edited = {"a": _claim(SCRUBBED, 1)}

    result = _merge(base, edited, fresh)

    assert result.items == {"a": _claim(SCRUBBED, 5)}
    assert _non_empty(result) == {"refreshed": ("a",)}
    assert result.changes.lost_edits == ()


def test_deleted_item_stays_deleted_when_only_refresh_keys_change() -> None:
    base, fresh = {"a": _claim("raw text", 1)}, {"a": _claim("raw text", 5)}

    result = _merge(base, {}, fresh)

    assert result.items == {}
    assert _non_empty(result) == {}


def test_scrubbed_text_is_superseded_when_the_step_changes_the_text_too() -> None:
    base, fresh = {"a": _claim("raw text", 1)}, {"a": _claim("new text", 5)}
    edited = {"a": _claim(SCRUBBED, 1)}

    result = _merge(base, edited, fresh)

    assert result.items == fresh
    assert _non_empty(result) == {"updated": ("a",), "superseded": ("a",)}
    assert result.changes.lost_edits == ("a",)


def test_deleted_item_returns_when_the_step_changes_the_text() -> None:
    result = _merge({"a": _claim("raw text")}, {}, {"a": _claim("new text")})

    assert result.items == {"a": _claim("new text")}
    assert _non_empty(result) == {"updated": ("a",), "resurrected": ("a",)}


# --- the full table: user (untouched / modified / deleted / created) x step

# Columns: base, edited, fresh, merged items, the non-empty change lists.
TABLE = [
    pytest.param(
        {"a": _claim()}, {"a": _claim()}, {"a": _claim()},
        {"a": _claim()}, {},
        id="untouched, step unchanged",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim()}, {"a": _claim(count=4)},
        {"a": _claim(count=4)}, {},
        id="untouched, only refresh keys changed: follows fresh, nothing to report",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim()}, {"a": _claim("v2", 4)},
        {"a": _claim("v2", 4)}, {"updated": ("a",)},
        id="untouched, text changed",
    ),
    pytest.param(
        {"a": _claim(), "b": _claim()}, {"a": _claim(), "b": _claim()}, {"a": _claim()},
        {"a": _claim()}, {"removed": ("b",)},
        id="untouched, removed by the step",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim()},
        {"a": _claim("hand")}, {},
        id="modified, step unchanged: kept as is, nothing refreshed",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim(count=4)},
        {"a": _claim("hand", 4)}, {"refreshed": ("a",)},
        id="modified, only refresh keys changed: kept and refreshed",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim("v2")},
        {"a": _claim("v2")}, {"updated": ("a",), "superseded": ("a",)},
        id="modified, text changed: superseded",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim("v2", 4)},
        {"a": _claim("v2", 4)}, {"updated": ("a",), "superseded": ("a",)},
        id="modified, text and refresh keys changed: superseded",
    ),
    pytest.param(
        {"a": _claim("v2")}, {"a": _claim("v2", 9)}, {"a": _claim("v2", 4)},
        {"a": _claim("v2", 4)}, {},
        id="modified in refresh keys only: not an edit, follows fresh",
    ),
    pytest.param(
        {"a": _claim(), "b": _claim()}, {"a": _claim(), "b": _claim("hand")}, {"a": _claim()},
        {"a": _claim()}, {"removed": ("b",), "dropped_edited": ("b",)},
        id="modified, removed by the step: dropped and recorded",
    ),
    pytest.param(
        {"a": _claim()}, {}, {"a": _claim()},
        {}, {},
        id="deleted, step unchanged: stays deleted",
    ),
    pytest.param(
        {"a": _claim(), "b": _claim()}, {"a": _claim()}, {"a": _claim(), "b": _claim()},
        {"a": _claim()}, {},
        id="deleted other item, step unchanged: stays deleted",
    ),
    pytest.param(
        {"a": _claim(), "b": _claim()}, {"a": _claim()}, {"a": _claim()},
        {"a": _claim()}, {"removed": ("b",)},
        id="deleted, removed by the step: nothing lost",
    ),
    pytest.param(
        {}, {"a": _claim("hand")}, {"a": _claim("v1")},
        {"a": _claim("v1")}, {"added": ("a",), "superseded": ("a",)},
        id="created id the step now produces with other text: superseded",
    ),
    pytest.param(
        {}, {"a": _claim("v1", 1)}, {"a": _claim("v1", 4)},
        {"a": _claim("v1", 4)}, {"added": ("a",)},
        id="created id the step now produces, same except refresh keys: nothing lost",
    ),
    pytest.param(
        {"a": _claim()}, {"a": _claim(), "mine": _claim("hand", 9)}, {"a": _claim(count=4)},
        {"a": _claim(count=4), "mine": _claim("hand", 9)}, {"user_created": ("mine",)},
        id="created id the step does not produce: kept untouched, not refreshed",
    ),
]


@pytest.mark.parametrize(("base", "edited", "fresh", "items", "changes"), TABLE)
def test_table_with_refresh_keys(
    base: dict[str, Any],
    edited: dict[str, Any],
    fresh: dict[str, Any],
    items: dict[str, Any],
    changes: dict[str, tuple[str, ...]],
) -> None:
    result = _merge(base, edited, fresh)

    assert result.items == items
    assert _non_empty(result) == changes


# --- how the kept item is refreshed


def test_refresh_key_missing_from_fresh_is_removed_and_new_ones_are_added() -> None:
    base = {"a": {"text": "v1", "count": 1, "dates": [1]}}
    edited = {"a": {"text": "hand", "count": 1, "dates": [1]}}
    fresh = {"a": {"text": "v1", "count": 2}}

    result = merge_edited(base, edited, fresh, refresh_keys=REFRESH)
    reverse = merge_edited(fresh, {"a": {"text": "hand", "count": 2}}, base, refresh_keys=REFRESH)

    assert result.items == {"a": {"text": "hand", "count": 2}}
    assert reverse.items == {"a": {"text": "hand", "count": 1, "dates": [1]}}
    assert result.changes.refreshed == reverse.changes.refreshed == ("a",)


def test_refreshing_keeps_the_key_order_of_the_user_item() -> None:
    base = {"a": {"count": 1, "text": "v1", "note": "n"}}
    edited = {"a": {"count": 1, "text": "hand", "note": "n"}}
    fresh = {"a": {"count": 2, "text": "v1", "note": "n"}}

    result = merge_edited(base, edited, fresh, refresh_keys=["count"])

    assert list(result.items["a"].items()) == [("count", 2), ("text", "hand"), ("note", "n")]


def test_is_same_sees_items_without_the_refresh_keys() -> None:
    seen: list[tuple[Any, Any]] = []

    def is_same(left: dict[str, Any], right: dict[str, Any]) -> bool:
        seen.append((left, right))
        return left == right

    merge_edited(
        {"a": _claim(score=1)}, {"a": _claim(score=1)}, {"a": _claim(count=4, score=1)},
        is_same=is_same, refresh_keys=REFRESH,
    )

    assert seen == [({"text": "v1", "score": 1}, {"text": "v1", "score": 1})]


def test_refresh_keys_compose_with_same_ignoring() -> None:
    base = {"a": _claim(score=1)}
    edited = {"a": _claim("hand", score=1)}
    fresh = {"a": _claim(count=4, score=2)}

    result = _merge(base, edited, fresh, is_same=same_ignoring("score"))

    # ``score`` is ignored but not refreshed: the user's value stays; ``count`` is refreshed.
    assert result.items == {"a": _claim("hand", 4, score=1)}
    assert _non_empty(result) == {"refreshed": ("a",)}


def test_merge_with_refresh_keys_leaves_its_inputs_untouched() -> None:
    base, edited, fresh = {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim(count=4)}

    _merge(base, edited, fresh)

    assert (base, edited, fresh) == ({"a": _claim()}, {"a": _claim("hand")}, {"a": _claim(count=4)})


def test_summary_counts_refreshed() -> None:
    result = _merge({"a": _claim()}, {"a": _claim("hand")}, {"a": _claim(count=4)})

    assert result.changes.summary().endswith("user_created=0, refreshed=1")


# --- refresh_keys=() is what it was; bad arguments


def test_empty_refresh_keys_behave_like_before_for_any_item_type() -> None:
    base, edited, fresh = {"a": "v1", "b": 1}, {"a": "hand", "b": 1}, {"a": "v1", "b": 2}

    plain = merge_edited(base, edited, fresh)

    assert merge_edited(base, edited, fresh, refresh_keys=()) == plain
    assert plain.items == {"a": "hand", "b": 2}
    assert plain.changes.refreshed == ()


def test_empty_refresh_keys_supersede_on_a_count_change_as_before() -> None:
    base, edited, fresh = {"a": _claim()}, {"a": _claim("hand")}, {"a": _claim(count=4)}

    result = merge_edited(base, edited, fresh, refresh_keys=())

    assert result.items == fresh
    assert result.changes.superseded == ("a",)


@pytest.mark.parametrize("where", ["base", "edited", "fresh"])
def test_non_dict_items_with_refresh_keys_raise(where: str) -> None:
    collections = {"base": {"a": _claim()}, "edited": {"a": _claim()}, "fresh": {"a": _claim()}}
    collections[where] = {"a": "just a string"}

    with pytest.raises(ValueError, match=rf"{where}\['a'\] is a str"):
        merge_edited(**collections, refresh_keys=REFRESH)


def test_a_bare_string_as_refresh_keys_is_rejected() -> None:
    with pytest.raises(TypeError, match="collection of names"):
        merge_edited({}, {}, {}, refresh_keys="count")


# --- write_reconciled end to end


@pytest.fixture(autouse=True)
def backups_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "backups"
    monkeypatch.setattr(reconcile, "BACKUPS_DIR", path)
    return path


@pytest.fixture
def artifact(tmp_path: Path) -> Artifact:
    return Artifact(tmp_path / "claims.json", tmp_path / "claims_edited.json")


def _file(run: int, *claims: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"run": run}, "claims": list(claims)}


def _row(claim_id: str, text: str, count: int = 1) -> dict[str, Any]:
    return {"claim_id": claim_id, "text": text, "count": count}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_reconciled_keeps_the_scrub_and_refreshes_the_counts_on_disk(
    artifact: Artifact, backups_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="utils.reconcile")
    original = _file(1, _row("scrubbed", "raw"), _row("deleted", "raw"), _row("plain", "raw"))
    edited = _file(1, _row("scrubbed", SCRUBBED), _row("plain", "raw"), _row("mine", "by hand"))
    write_json_file(original, artifact.original)
    write_json_file(edited, artifact.edited)
    payload = _file(
        2, _row("scrubbed", "raw", 7), _row("deleted", "raw", 7), _row("plain", "raw", 7)
    )

    report = write_reconciled(
        artifact, payload, items_key="claims", id_key="claim_id", refresh_keys=["count"]
    )

    assert _read(artifact.edited) == _file(
        2, _row("scrubbed", SCRUBBED, 7), _row("plain", "raw", 7), _row("mine", "by hand")
    )
    assert _read(artifact.original) == payload
    (backup,) = backups_dir.iterdir()
    assert _read(backup) == edited
    assert report.changes.refreshed == ("scrubbed",)
    assert report.changes.user_created == ("mine",)
    assert report.changes.lost_edits == ()
    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert "refreshed=1" in record.getMessage()


def test_write_reconciled_supersedes_when_the_text_changed_as_well(artifact: Artifact) -> None:
    write_json_file(_file(1, _row("a", "raw")), artifact.original)
    write_json_file(_file(1, _row("a", SCRUBBED)), artifact.edited)
    payload = _file(2, _row("a", "raw, reworded", 3))

    report = write_reconciled(
        artifact, payload, items_key="claims", id_key="claim_id", refresh_keys=["count"]
    )

    assert _read(artifact.edited) == payload
    assert report.changes.superseded == ("a",)


def test_write_reconciled_without_refresh_keys_still_supersedes_on_a_count_change(
    artifact: Artifact,
) -> None:
    write_json_file(_file(1, _row("a", "raw")), artifact.original)
    write_json_file(_file(1, _row("a", SCRUBBED)), artifact.edited)
    payload = _file(2, _row("a", "raw", 3))

    report = write_reconciled(artifact, payload, items_key="claims", id_key="claim_id")

    assert _read(artifact.edited) == payload
    assert report.changes.superseded == ("a",)


def test_write_reconciled_with_non_dict_items_and_refresh_keys_writes_nothing(
    artifact: Artifact, backups_dir: Path
) -> None:
    write_json_file({"pages": {"a": "text"}}, artifact.original)
    write_json_file({"pages": {"a": "by hand"}}, artifact.edited)
    before = (artifact.original.read_bytes(), artifact.edited.read_bytes())

    with pytest.raises(ValueError, match="need dict items"):
        write_reconciled(
            artifact, {"pages": {"a": "text v2"}}, items_key="pages", id_key=None,
            refresh_keys=["count"],
        )

    assert (artifact.original.read_bytes(), artifact.edited.read_bytes()) == before
    assert not backups_dir.exists()
