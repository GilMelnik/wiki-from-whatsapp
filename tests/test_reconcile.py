"""Pure logic of ``utils.reconcile``: fingerprints, input diff, three-way merge.

The file-writing half (``write_reconciled``, ``guarded_write``, backups, locks) is covered in
``tests/test_reconcile_write.py``.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict
from typing import Any

import pytest

from utils.reconcile import InputDiff, diff_inputs, fingerprint, merge_edited, same_ignoring

SCORED_BASE = {"a": {"label": "x", "p": 0.81}, "b": {"label": "y", "p": 0.4}}
SCORED_EDITED = {"a": {"label": "by hand", "p": 0.81}, "b": {"label": "y", "p": 0.4}}
SCORED_FRESH = {"a": {"label": "x", "p": 0.83}, "b": {"label": "y", "p": 0.5}}

MIXED_BASE = {"kept": 1, "edited": 1, "deleted": 1, "gone": 1}
MIXED_EDITED = {"kept": "by hand", "edited": "by hand", "gone": "by hand", "mine": "by hand"}
MIXED_FRESH = {"kept": 1, "edited": 2, "deleted": 2}


def test_fingerprint_ignores_key_order_at_every_level() -> None:
    one = {"a": 1, "b": {"x": [1, 2], "y": "שלום"}}
    other = {"b": {"y": "שלום", "x": [1, 2]}, "a": 1}

    assert fingerprint(one) == fingerprint(other)


def test_fingerprint_is_sha1_of_compact_unescaped_json() -> None:
    expected = hashlib.sha1('{"a":"שלום","b":[1,2]}'.encode()).hexdigest()

    assert fingerprint({"b": [1, 2], "a": "שלום"}) == expected


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ({"text": "שלום"}, {"text": "שלומ"}),
        ({"ids": [1, 2]}, {"ids": [2, 1]}),
        ({"n": 1}, {"n": "1"}),
        ({"a": {"b": 1}}, {"a": {"b": 2}}),
    ],
)
def test_fingerprint_differs_for_different_values(one: Any, other: Any) -> None:
    assert fingerprint(one) != fingerprint(other)


def test_diff_inputs_splits_ids_into_the_four_classes_in_order() -> None:
    previous = {"gone2": "x", "same": "s", "changed": "old", "gone1": "y"}
    current = {"new2": "n", "changed": "new", "same": "s", "new1": "n"}

    assert diff_inputs(previous, current) == InputDiff(
        new=("new2", "new1"),
        changed=("changed",),
        removed=("gone2", "gone1"),
        unchanged=("same",),
    )


def test_diff_inputs_with_no_previous_run_marks_everything_new() -> None:
    assert diff_inputs({}, {"a": "1", "b": "2"}) == InputDiff(
        new=("a", "b"), changed=(), removed=(), unchanged=()
    )


def test_diff_inputs_with_empty_current_marks_everything_removed() -> None:
    assert diff_inputs({"a": "1", "b": "2"}, {}) == InputDiff(
        new=(), changed=(), removed=("a", "b"), unchanged=()
    )


# Columns: base, edited, fresh, merge options, merged items, the non-empty change lists.
MERGE_CASES = [
    pytest.param(
        {"a": "v1"}, {"a": "by hand"}, {"a": "v1"}, {},
        {"a": "by hand"}, {},
        id="unchanged output: manual edit kept",
    ),
    pytest.param(
        {"a": "v1", "b": "v1"}, {"b": "v1"}, {"a": "v1", "b": "v1"}, {},
        {"b": "v1"}, {},
        id="unchanged output: manual deletion kept",
    ),
    pytest.param(
        {"a": "v1"}, {"a": "v1"}, {"a": "v1", "b": "v1"}, {},
        {"a": "v1", "b": "v1"}, {"added": ("b",)},
        id="new item: added",
    ),
    pytest.param(
        {"a": "v1"}, {"a": "v1"}, {"a": "v2"}, {},
        {"a": "v2"}, {"updated": ("a",)},
        id="changed item the user did not touch: refreshed",
    ),
    pytest.param(
        {"a": "v1"}, {"a": "by hand"}, {"a": "v2"}, {},
        {"a": "v2"}, {"updated": ("a",), "superseded": ("a",)},
        id="changed item with a manual edit: superseded",
    ),
    pytest.param(
        {"a": "v1"}, {}, {"a": "v2"}, {},
        {"a": "v2"}, {"updated": ("a",), "resurrected": ("a",)},
        id="changed item the user deleted: resurrected",
    ),
    pytest.param(
        {}, {"a": "by hand"}, {"a": "v1"}, {},
        {"a": "v1"}, {"added": ("a",), "superseded": ("a",)},
        id="user-created id the step now produces: superseded",
    ),
    pytest.param(
        {"a": "v1"}, {"a": "v2"}, {"a": "v2"}, {},
        {"a": "v2"}, {"updated": ("a",)},
        id="manual edit equal to the fresh item: nothing lost",
    ),
    pytest.param(
        {"a": "v1", "b": "v1"}, {"a": "v1", "b": "v1"}, {"a": "v1"}, {},
        {"a": "v1"}, {"removed": ("b",)},
        id="removed item: dropped",
    ),
    pytest.param(
        {"a": "v1", "b": "v1"}, {"a": "v1", "b": "by hand"}, {"a": "v1"}, {},
        {"a": "v1"}, {"removed": ("b",), "dropped_edited": ("b",)},
        id="removed item with a manual edit: dropped and recorded",
    ),
    pytest.param(
        {"a": "v1", "b": "v1"}, {"a": "v1"}, {"a": "v1"}, {},
        {"a": "v1"}, {"removed": ("b",)},
        id="removed item the user deleted: nothing lost",
    ),
    pytest.param(
        {"a": "v1"}, {"mine": "by hand", "a": "v1"}, {"a": "v1"}, {},
        {"a": "v1", "mine": "by hand"}, {"user_created": ("mine",)},
        id="user-created item: kept",
    ),
    pytest.param(
        SCORED_BASE, SCORED_EDITED, SCORED_FRESH, {},
        SCORED_FRESH, {"updated": ("a", "b"), "superseded": ("a",)},
        id="volatile field changed, default is_same: manual edit superseded",
    ),
    pytest.param(
        SCORED_BASE, SCORED_EDITED, SCORED_FRESH, {"is_same": same_ignoring("p")},
        {"a": SCORED_EDITED["a"], "b": SCORED_FRESH["b"]}, {},
        id="volatile field ignored: manual edit kept, untouched item follows fresh",
    ),
    pytest.param(
        MIXED_BASE, MIXED_EDITED, MIXED_FRESH, {},
        {"kept": "by hand", "edited": 2, "deleted": 2, "mine": "by hand"},
        {
            "updated": ("edited", "deleted"),
            "removed": ("gone",),
            "superseded": ("edited",),
            "resurrected": ("deleted",),
            "dropped_edited": ("gone",),
            "user_created": ("mine",),
        },
        id="all rows at once",
    ),
]


@pytest.mark.parametrize(("base", "edited", "fresh", "options", "items", "changes"), MERGE_CASES)
def test_merge_edited(
    base: dict[str, Any],
    edited: dict[str, Any],
    fresh: dict[str, Any],
    options: dict[str, Any],
    items: dict[str, Any],
    changes: dict[str, tuple[str, ...]],
) -> None:
    result = merge_edited(base, edited, fresh, **options)

    assert result.items == items
    assert {name: ids for name, ids in asdict(result.changes).items() if ids} == changes


def test_merge_orders_fresh_items_first_then_user_created_in_their_order() -> None:
    base = {"a": 1, "b": 1, "c": 1}
    edited = {"mine2": 0, "c": 1, "mine1": 0, "b": 1, "a": 1}
    fresh = {"c": 1, "new": 1, "a": 1, "b": 1}

    result = merge_edited(base, edited, fresh)

    assert list(result.items) == ["c", "new", "a", "b", "mine2", "mine1"]


def test_lost_edits_lists_every_manual_change_that_was_replaced() -> None:
    result = merge_edited(MIXED_BASE, MIXED_EDITED, MIXED_FRESH)

    assert result.changes.lost_edits == ("edited", "deleted", "gone")


def test_merge_leaves_its_inputs_untouched() -> None:
    base, edited, fresh = copy.deepcopy((SCORED_BASE, SCORED_EDITED, SCORED_FRESH))

    merge_edited(base, edited, fresh, is_same=same_ignoring("p"))

    assert (base, edited, fresh) == (SCORED_BASE, SCORED_EDITED, SCORED_FRESH)
