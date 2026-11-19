"""What the same-complaint check releases (``step_7_write_pages.same_complaint_release``) and the
backers of a claim (``step_6_plan.claim_records.claim_backers``). Synthetic claims and people."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from step_6_plan.claim_records import claim_backers, load_claim_records
from step_7_write_pages.same_complaint_release import (
    ANSWERED,
    NO_CALL,
    UNUSABLE,
    WAITING,
    Check,
    entity_lists,
    list_fingerprint,
    load_answers,
    load_vetoes,
    released_ids,
    save_vetoes,
    withheld_by_rule,
)
from tests.same_complaint_support import World, person
from utils.json_io import read_json_file, write_json_file


class Setup:
    """A world of claims plus the answers and vetoes files of the check, under ``root``."""

    def __init__(self, root: Path) -> None:
        self.world = World(root)
        self.answers = root / "same_complaint.json"
        self.vetoes = root / "vetoes.json"

    def records(self) -> list[dict[str, Any]]:
        self.sources = self.world.write()
        return load_claim_records(self.sources)

    def answer(self, entity_id: str, *groups: list[str], problem: str | None = None,
               fingerprint: str | None = None) -> None:
        """Store an answer for the entity's current list (or the given fingerprint)."""

        records = load_claim_records(self.sources)
        stored = load_answers(self.answers)
        stored[entity_id] = {
            "fingerprint": fingerprint or list_fingerprint(entity_lists(records)[entity_id]),
            "groups": [{"label": "same problem", "claims": list(g)} for g in groups],
            "problem": problem, "answered_at": "2026-11-19T00:00:00+00:00",
        }
        write_json_file({"entities": stored, "metadata": {}}, self.answers)

    def check(self) -> Check:
        records = load_claim_records(self.sources)
        return Check.load(records, audit=self.sources.audit, answers=self.answers,
                          vetoes=self.vetoes)

    def released(self) -> frozenset[str]:
        return released_ids(load_claim_records(self.sources), audit=self.sources.audit,
                            answers=self.answers, vetoes=self.vetoes)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_backers_follow_the_supporter_count_rule(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1, 2])
    b = setup.world.add(["e0001"], [3])
    c = setup.world.add([], [])
    records = setup.records()
    audit = read_json_file(setup.sources.audit)
    # person 2 later disputed claim a: they count on the opposing side, as in opposer_count
    audit["audit"][0]["opposers"] = [{"person": person(2), "date": "2024-05-01"}]
    write_json_file(audit, setup.sources.audit)
    records = load_claim_records(setup.sources)
    backers = claim_backers(records, setup.sources.audit)
    assert backers == {a: {person(1)}, b: {person(3)}, c: frozenset()}
    assert [r["supporter_count"] for r in records] == [len(backers[r["short_id"]]) for r in records]
    with pytest.raises(FileNotFoundError, match="private audit"):
        claim_backers(records, setup.sources.audit.with_name("missing.json"))


def test_the_lists_hold_the_negative_claims_of_an_entity_in_file_order(setup: Setup) -> None:
    a = setup.world.add(["e0001", "e0002"], [1])
    setup.world.add(["e0001"], [2], stance="positive")
    c = setup.world.add(["e0001"], [2, 3])
    d = setup.world.add([], [4])
    records = setup.records()
    lists = entity_lists(records)
    assert {e: [r["short_id"] for r in claims] for e, claims in lists.items()} == {
        "e0001": [a, c], "e0002": [a]}
    assert [withheld_by_rule(r) for r in records] == [True, False, False, False]
    assert d not in {r["short_id"] for claims in lists.values() for r in claims}
    check = setup.check()
    # e0001: a is withheld and two other people back c; e0002: one person in all
    assert check.states == {"e0001": WAITING, "e0002": NO_CALL}
    assert set(check.asked()) == {"e0001"}


def test_two_people_making_the_same_complaint_release_each_other(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1])
    b = setup.world.add(["e0001"], [2])
    c = setup.world.add(["e0001"], [3])
    setup.records()
    assert setup.released() == frozenset()
    setup.answer("e0001", [b, a])
    check = setup.check()
    assert check.states == {"e0001": ANSWERED}
    assert setup.released() == {a, b}
    (group,) = check.groups
    assert (group.claims, group.people, group.released) == ((a, b), 2, (a, b))
    assert c not in setup.released()


def test_one_person_complaining_twice_releases_nothing(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1])
    b = setup.world.add(["e0001"], [1])
    setup.world.add(["e0001"], [2])
    setup.records()
    setup.answer("e0001", [a, b])
    assert setup.released() == frozenset()
    assert setup.check().groups[0].people == 1


def test_a_claim_already_published_releases_the_lone_one(setup: Setup) -> None:
    lone = setup.world.add(["e0001"], [1])
    backed = setup.world.add(["e0001"], [1, 2])
    setup.records()
    setup.answer("e0001", [lone, backed])
    assert setup.released() == {lone}  # the other one was never withheld


def test_a_changed_list_releases_nothing_until_it_is_answered_again(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1], text="First text.")
    b = setup.world.add(["e0001"], [2])
    setup.records()
    setup.answer("e0001", [a, b])
    assert setup.released() == {a, b}
    setup.world.records[0]["claim_text"] = "Edited text."
    setup.records()
    assert setup.released() == frozenset()
    assert setup.check().states == {"e0001": WAITING}
    setup.world.records[0]["claim_text"] = "First text."
    setup.world.add(["e0001"], [3])  # a new claim joins the list
    setup.records()
    assert setup.released() == frozenset()


@pytest.mark.parametrize("groups, problem", [
    ([["g-unknown0", "A"]], None),
    ([["A", "B"], ["B", "C"]], None),
    ([["A"]], None),
    ([["A", "A"]], None),
    ([["A", "B"]], "the model's answer did not parse"),
])
def test_an_unusable_answer_releases_nothing_of_its_entity(
        setup: Setup, groups: list[list[str]], problem: str | None,
        caplog: pytest.LogCaptureFixture) -> None:
    ids = {"A": setup.world.add(["e0001"], [1]), "B": setup.world.add(["e0001"], [2]),
           "C": setup.world.add(["e0001"], [3])}
    other = [setup.world.add(["e0002"], [4]), setup.world.add(["e0002"], [5])]
    setup.records()
    setup.answer("e0001", *[[ids.get(c, c) for c in g] for g in groups], problem=problem)
    setup.answer("e0002", other)
    with caplog.at_level(logging.WARNING):
        check = setup.check()
    assert check.states == {"e0001": UNUSABLE, "e0002": ANSWERED}
    assert frozenset(check.released) == set(other)
    assert "e0001" in caplog.text and "unusable" in caplog.text


def test_a_missing_file_releases_nothing_and_a_broken_one_raises(setup: Setup) -> None:
    setup.world.add(["e0001"], [1])
    setup.records()
    assert setup.released() == frozenset()
    write_json_file({"answers": []}, setup.answers)
    with pytest.raises(ValueError, match="answers file"):
        setup.released()


def test_one_entity_list_is_enough_to_release(setup: Setup) -> None:
    a = setup.world.add(["e0001", "e0002"], [1])
    b = setup.world.add(["e0001"], [2])
    c = setup.world.add(["e0002"], [3])
    setup.records()
    setup.answer("e0001", [a, b])
    setup.answer("e0002")  # about the clinic, a and c say different things
    assert setup.released() == {a, b}
    assert c not in setup.released()


def test_a_claim_kept_withheld_is_never_released_and_backs_nobody(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1])
    b = setup.world.add(["e0001"], [2])
    c = setup.world.add(["e0001"], [3])
    d = setup.world.add(["e0001"], [4])
    setup.records()
    setup.answer("e0001", [a, b], [c, d])
    save_vetoes([a], setup.vetoes)
    # b's only other person was a's: b stays withheld; c and d are not touched
    assert setup.released() == {c, d}
    group = next(g for g in setup.check().groups if a in g.claims)
    assert (group.people, group.people_unmarked, group.released) == (1, 2, ())
    save_vetoes([], setup.vetoes)
    assert setup.released() == {a, b, c, d}


def test_three_people_survive_one_mark(setup: Setup) -> None:
    a = setup.world.add(["e0001"], [1])
    b = setup.world.add(["e0001"], [2])
    c = setup.world.add(["e0001"], [3])
    setup.records()
    setup.answer("e0001", [a, b, c])
    save_vetoes([b], setup.vetoes)
    assert setup.released() == {a, c}


def test_the_vetoes_file(setup: Setup) -> None:
    assert load_vetoes(setup.vetoes) == frozenset()
    save_vetoes(["g-b", "g-a", "g-b"], setup.vetoes)
    stored = read_json_file(setup.vetoes)
    assert stored["keep_withheld"] == ["g-a", "g-b"]
    assert stored["metadata"]["count"] == 2
    assert load_vetoes(setup.vetoes) == {"g-a", "g-b"}
    write_json_file({"keep_withheld": "g-a"}, setup.vetoes)
    with pytest.raises(ValueError, match="keep withheld"):
        load_vetoes(setup.vetoes)
