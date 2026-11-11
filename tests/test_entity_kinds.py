"""Entity kinds (task 29): set by the classifier in step 4, kept, refreshed, overridden by the
owner in the entities tool. Synthetic claims and a fake classifier only (no paid call)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from step_4_entities.kinds import EntityKinds, kind_state, merge_kind, pick_kind
from step_4_entities.run import run
from tests.entities_tool_support import (
    EntitiesSetup,
    FakeKindClassifier,
    claim,
    claims_payload,
)
from tests.review_tool_support import locked_elsewhere
from utils.json_io import write_json_file
from utils.paths import Artifact
from utils.reconcile import fingerprint

LOGGER = logging.getLogger("test-kinds")


def _person_if(name: str) -> Any:
    return lambda state: "person" if f"Name: {name}" in state else "organisation"


class Kinds:
    """Step 4 (with a fake kind classifier) and the entities tool on the same files."""

    def __init__(self, root: Path) -> None:
        self.setup = EntitiesSetup(root, with_entities=False)
        self.root = root

    def step_4(self, classifier: FakeKindClassifier) -> dict[str, Any]:
        return run(claims_path=self.setup.claims_path,
                   artifact=Artifact(self.setup.original, self.setup.edited),
                   analysis_path=self.setup.cache, seed_path=self.root / "seed.json",
                   classifier=classifier)

    def by_name(self) -> dict[str, dict[str, Any]]:
        return {e["canonical_name"]: e for e in self.setup.saved()["entities"]}


@pytest.fixture
def kinds(tmp_path: Path) -> Kinds:
    return Kinds(tmp_path)


def test_step_4_gives_every_entity_a_kind(kinds: Kinds) -> None:
    fake = FakeKindClassifier(_person_if("David Shield"))
    result = kinds.step_4(fake)
    entities = kinds.by_name()
    assert result["kinds_classified"] == len(entities) == len(fake.states)
    shield = entities["David Shield"]
    assert (shield["kind"], shield["kind_confidence"]) == ("person", 0.9)
    assert {e["kind"] for name, e in entities.items() if name != "David Shield"} == {"organisation"}
    state = next(s for s in fake.states if "Name: David Shield" in s)
    assert "Other spellings: DavidShield" in state and "- David Shield sells travel insurance" in state
    assert shield["kind_input"] == fingerprint(state) and not shield.get("kind_manual")
    meta = kinds.setup.saved()["metadata"]["kinds"]
    assert meta["person"] == 1 and meta["unknown"] == 0 and meta["manual"] == 0


def test_kinds_are_kept_and_asked_again_only_when_what_was_read_changed(kinds: Kinds) -> None:
    kinds.step_4(FakeKindClassifier())
    before = kinds.setup.edited.read_bytes()
    again = FakeKindClassifier(lambda state: "place")
    result = kinds.step_4(again)
    assert (again.states, result["kinds_classified"], result["backup"]) == ([], 0, None)
    assert kinds.setup.edited.read_bytes() == before  # nothing written
    extra = [claim("t-N-0001", "A new office in Tel Aviv", ["Tel Aviv"])]
    write_json_file(claims_payload(extra), kinds.setup.claims_path)
    kinds.step_4(again)
    assert len(again.states) == 1 and "Name: Tel Aviv" in again.states[0]  # refreshed
    entities = kinds.by_name()
    assert entities["Tel Aviv"]["kind"] == "place"
    assert entities["David Shield"]["kind"] == "organisation"  # kept


def test_the_owners_kind_wins_over_every_later_run(kinds: Kinds) -> None:
    kinds.step_4(FakeKindClassifier())
    shield = kinds.by_name()["David Shield"]["entity_id"]
    answer = kinds.setup.post(f"/entities/{shield}/kind", {"kind": "person"})
    assert answer["entity_id"] == shield
    saved = kinds.setup.saved_entity(shield)
    assert (saved["kind"], saved["kind_manual"]) == ("person", True)
    assert "kind_confidence" not in saved and "kind_input" not in saved
    detail = kinds.setup.entity(shield)
    assert (detail["kind"], detail["kind_manual"], detail["kind_confidence"]) == ("person", True, None)
    extra = [claim("t-N-0002", "David Shield answered on the phone", ["David Shield"])]
    write_json_file(claims_payload(extra), kinds.setup.claims_path)
    later = FakeKindClassifier()
    kinds.step_4(later)
    assert not any("Name: David Shield" in state for state in later.states)
    assert kinds.setup.saved_entity(shield)["kind"] == "person"


def test_an_unsure_answer_is_a_person_and_a_failure_leaves_no_kind(kinds: Kinds) -> None:
    assert pick_kind({"a": 0.2}) == ("person", 0.2)  # an unknown label: when unsure, a person
    kinds.step_4(FakeKindClassifier(lambda state: "place", confidence=0.45))
    assert {e["kind"] for e in kinds.by_name().values()} == {"person"}
    entities = [{"entity_id": "e1", "canonical_name": "X", "aliases": ["X"], "members": []}]
    stats = EntityKinds(FakeKindClassifier(fail=True), {}, LOGGER).apply(entities)
    assert stats == {"kinds_classified": 0, "kinds_failed": 1} and "kind" not in entities[0]


def test_the_kind_endpoint_refuses_what_does_not_fit(kinds: Kinds) -> None:
    kinds.step_4(FakeKindClassifier())
    entity_id = kinds.by_name()["ORM"]["entity_id"]
    before = kinds.setup.edited.read_bytes()
    kinds.setup.post(f"/entities/{entity_id}/kind", {"kind": "company"}, expect=422)
    kinds.setup.post(f"/entities/{entity_id}/kind", {}, expect=422)
    assert "e9999" in kinds.setup.post("/entities/e9999/kind", {"kind": "person"}, expect=404)["detail"]
    with locked_elsewhere(kinds.setup.edited):
        response = kinds.setup.client.post(f"/api/entities/entities/{entity_id}/kind",
                                           json={"kind": "person"})
    assert response.status_code == 423
    assert kinds.setup.edited.read_bytes() == before


def test_a_merge_keeps_the_owners_kind_and_drops_two_different_automatic_ones() -> None:
    def entity(kind: str | None, manual: bool = False) -> dict[str, Any]:
        found: dict[str, Any] = {"kind": kind, "kind_confidence": 0.8, "kind_input": "fp"} if kind else {}
        return {**found, "kind_manual": True} if manual else found

    for target, source, expected in (
        (entity("organisation", manual=True), entity("person", manual=True), ("organisation", True)),
        (entity("organisation"), entity("person", manual=True), ("person", True)),
        (entity("organisation"), entity("organisation"), ("organisation", None)),
        (entity("organisation"), entity("person"), (None, None)),
        (entity("organisation"), entity(None), (None, None)),
    ):
        merge_kind(target, source)
        assert (target.get("kind"), target.get("kind_manual")) == expected


def test_a_merge_in_the_tool_leaves_the_kind_to_ask_again(kinds: Kinds) -> None:
    kinds.step_4(FakeKindClassifier(_person_if("David Shield")))
    entities = kinds.by_name()
    shield, orm = entities["David Shield"]["entity_id"], entities["ORM"]["entity_id"]
    kinds.setup.post(f"/entities/{orm}/merge", {"target_entity_id": shield})
    assert "kind" not in kinds.setup.saved_entity(shield)  # unknown: a person for the rule
    assert kinds.setup.entity(shield)["kind"] is None
    later = FakeKindClassifier()
    kinds.step_4(later)
    assert len(later.states) == 1 and kinds.setup.saved_entity(shield)["kind"] == "organisation"


def test_the_state_quotes_the_words_around_each_mention() -> None:
    entity = {"canonical_name": "Beta", "aliases": ["Beta", "בטא"],
              "members": [{"name": "Beta", "sample_claim_ids": ["c1", "c2"]},
                          {"name": "בטא", "sample_claim_ids": ["c3"]}]}
    claims = {"c1": {"claim_text": "x " * 100 + "Dr. Beta answered quickly" + " y" * 100},
              "c2": {"claim_text": "tagged but not named"}, "c3": {"claim_text": "עו\"ד בטא עזר"}}
    state = kind_state(entity, claims)
    lines = state.splitlines()
    assert lines[:3] == ["Name: Beta", "Other spellings: בטא", "How the members' messages mention it:"]
    assert "Dr. Beta answered quickly" in lines[3] and len(lines[3]) < 160
    assert lines[4:] == ["- tagged but not named", "- עו\"ד בטא עזר"]
