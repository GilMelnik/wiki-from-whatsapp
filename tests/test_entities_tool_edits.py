"""Entity review edits through the API: what each one changes in the saved entities file and
what step 5 reads from it (``claim_entities``, ids, deleted claims / names). Synthetic data;
the meaning of the old store tests is kept here."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_4_entities.records import claim_entity_index
from tests.entities_tool_support import DS1, DS2, DS3, DS4, DS5, DS6, EntitiesSetup, claim


@pytest.fixture
def tool(tmp_path: Path) -> EntitiesSetup:
    return EntitiesSetup(tmp_path)


def consistent(saved: dict) -> None:
    """``claim_entities`` and the id counter match the entities as saved."""
    assert saved["claim_entities"] == claim_entity_index(
        saved["entities"], deleted_claims=saved.get("deleted_claims") or ()
    )
    numbers = [int(e["entity_id"][1:]) for e in saved["entities"]]
    assert saved["metadata"]["last_entity_number"] >= max(numbers)
    assert saved["metadata"]["entity_count"] == len(saved["entities"])


def test_merge_combines_members_and_updates_claim_entities(tool: EntitiesSetup) -> None:
    tool.post(
        "/entities/e0001/claims/copy",
        {"name": "ORM", "claim_ids": [DS2], "target_entity_id": "e0002"},
    )
    answer = tool.post("/entities/e0002/merge", {"target_entity_id": "e0001"})
    assert answer["entity_id"] == "e0001"
    saved = tool.saved()
    consistent(saved)
    assert "e0002" not in {e["entity_id"] for e in saved["entities"]}
    names = [m["name"] for m in tool.saved_entity("e0001")["members"]]
    assert sorted(names) == ["ORM", "Tel Aviv"]  # the copied "ORM" member was combined
    assert saved["claim_entities"]["t-A-0004"] == ["e0001"]
    assert "conflict_with" in tool.saved_entity("e0001")  # the look-alike flag came along
    tool.get("/entities/e0002", expect=404)


def test_reject_splits_and_keeps_only_tagged_claims(tool: EntitiesSetup) -> None:
    answer = tool.post("/entities/e0000/reject")
    saved = tool.saved()
    consistent(saved)
    assert saved["metadata"]["last_entity_number"] == 4  # two fresh ids from the allocator
    by_name = {e["canonical_name"]: e for e in saved["entities"]}
    assert answer["entity_id"] == by_name["David Shield"]["entity_id"] == "e0003"
    assert by_name["DavidShield"]["status"] == "suggested"
    shield = by_name["David Shield"]["members"][0]
    assert shield["claim_ids"] == [DS1, DS2, DS6]  # the text-only mention (DS5) is gone
    assert shield["count"] == 3
    assert "attached_by" not in by_name["DavidShield"]["members"][0]
    assert saved["claim_entities"][DS5] == ["e0001"]  # only ORM tags it


def test_move_member_to_a_new_entity_takes_the_next_id(tool: EntitiesSetup) -> None:
    answer = tool.post("/entities/e0000/members/move", {"name": "DavidShield"})
    assert answer["entity_id"] == "e0000"  # the source still exists: it stays shown
    saved = tool.saved()
    consistent(saved)
    created = tool.saved_entity("e0003")
    assert (created["status"], created["aliases"]) == ("accepted", ["DavidShield"])
    assert "attached_by" not in created["members"][0]  # the owner placed it
    assert saved["claim_entities"][DS3] == ["e0003"]
    tool.post("/entities/e0003/members/move", {"name": "DavidShield", "target_entity_id": "e0000"})
    tool.get("/entities/e0003", expect=404)  # emptied entities are dropped
    assert tool.saved()["metadata"]["last_entity_number"] == 3  # ids are never reused


def test_confirm_an_attached_member(tool: EntitiesSetup) -> None:
    tool.post("/entities/e0000/members/confirm", {"name": "DavidShield"})
    assert tool.member("e0000", "DavidShield")["attached_by"] is None
    tool.post("/entities/e0000/members/confirm", {"name": "DavidShield"}, expect=409)


def test_move_copy_exclude_and_include_claims(tool: EntitiesSetup) -> None:
    tool.post(
        "/entities/e0000/claims/copy",
        {"name": "David Shield", "claim_ids": [DS1], "target_entity_id": "e0001"},
    )
    assert DS1 in tool.shown_claims("e0000", "David Shield")  # a copy keeps the source
    assert DS1 in tool.shown_claims("e0001", "David Shield")
    tool.post("/entities/e0000/claims/move", {"name": "David Shield", "claim_ids": [DS2]})
    assert DS2 not in tool.shown_claims("e0000", "David Shield")
    assert tool.saved_entity("e0003")["members"][0]["claim_ids"] == [DS2]
    tool.post("/entities/e0000/claims/exclude", {"name": "David Shield", "claim_ids": [DS5, DS6]})
    member = tool.member("e0000", "David Shield")
    assert [c["claim_id"] for c in member["excluded_claims"]] == [DS5, DS6]
    assert [c["claim_id"] for c in member["claims"]["claims"]] == [DS1]
    tool.post("/entities/e0000/claims/include", {"name": "David Shield", "claim_ids": [DS6]})
    excluded = tool.member("e0000", "David Shield")["excluded_claims"]
    assert excluded == [{"claim_id": DS5, "claim_text": "David Shield is great"}]
    saved = tool.saved()
    consistent(saved)
    assert saved["claim_entities"][DS5] == ["e0001"]  # excluded from David Shield, ORM stays
    body = {"name": "David Shield", "claim_ids": [DS5], "target_entity_id": "e0001"}
    tool.post("/entities/e0000/claims/move", body, expect=409)  # excluded: not the member's


def test_rename_sets_the_manual_name_and_follows_conflicts(tool: EntitiesSetup) -> None:
    tool.post("/entities/e0000/rename", {"canonical_name": "DavidShield"})
    saved = tool.saved_entity("e0000")
    assert (saved["canonical_name"], saved["canonical_manual"]) == ("DavidShield", True)
    assert tool.saved_entity("e0002")["conflict_with"] == ["DavidShield"]
    tool.post("/entities/e0000/members/move", {"name": "DavidShield"})
    assert tool.saved_entity("e0000")["canonical_name"] == "DavidShield"  # manual: kept


def test_manual_contacts_survive_a_member_move(tool: EntitiesSetup) -> None:
    contacts = {
        "email": ["support@shield.example", "support@shield.example"],
        "phone": [" +1-555-0100 "],
        "website": [],
    }
    tool.post("/entities/e0000/contacts", contacts)
    entity = tool.entity("e0000")
    assert entity["contacts"]["email"] == ["support@shield.example"]
    assert entity["contacts"]["phone"] == ["+1-555-0100"]
    assert entity["contacts_manual"] is True
    tool.post("/entities/e0000/members/move", {"name": "DavidShield", "target_entity_id": "e0001"})
    assert tool.entity("e0000")["contacts"]["email"] == ["support@shield.example"]


def test_uncertain_contacts_accept_reject_and_restore(tool: EntitiesSetup) -> None:
    value = "maybe@shared.com"
    accepted = {
        "kind": "email",
        "value": value,
        "action": "accept",
        "new_value": "desk@orm.example",
    }
    tool.post("/entities/e0001/contact", accepted)
    orm = tool.entity("e0001")
    assert (orm["contacts"]["email"], orm["contacts_uncertain"]["email"]) == (
        ["desk@orm.example"],
        [],
    )
    assert orm["contacts_manual"] is True

    tool.post("/entities/e0002/contact", {"kind": "email", "value": value, "action": "reject"})
    tel_aviv = tool.entity("e0002")
    assert tel_aviv["contacts_rejected"]["email"] == [value]
    tool.post("/entities/e0002/rename", {"canonical_name": "TLV"})  # a later edit: no comeback
    tool.post("/entities/e0000/merge", {"target_entity_id": "e0002"})  # a refresh: no comeback
    assert tool.entity("e0002")["contacts_uncertain"]["email"] == []

    tool.post("/entities/e0002/contact", {"kind": "email", "value": value, "action": "restore"})
    restored = tool.entity("e0002")
    assert (restored["contacts_rejected"]["email"], restored["contacts_uncertain"]["email"]) == (
        [],
        [value],
    )
    for body in (
        {"kind": "email", "value": "absent@x.example", "action": "accept"},
        {"kind": "email", "value": value, "action": "restore"},
    ):
        tool.post("/entities/e0002/contact", body, expect=409)


def test_delete_entity_records_names_and_can_be_undone(tool: EntitiesSetup) -> None:
    answer = tool.delete("/entities/e0001", sort="id", order="asc")
    assert answer["entity_id"] == "e0002"  # the one now in its place in that view
    saved = tool.saved()
    consistent(saved)
    assert saved["deleted_names"] == ["ORM"]
    assert tool.get("/deleted-names") == {"names": [{"name": "ORM", "claim_count": 3}]}
    restored = tool.post("/deleted-names/restore", {"name": "ORM"})["entity_id"]
    assert restored == "e0003"
    saved = tool.saved()
    assert "deleted_names" not in saved
    assert tool.saved_entity("e0003")["members"][0]["claim_ids"] == [DS2, DS4, DS5]
    tool.post("/deleted-names/restore", {"name": "ORM"}, expect=409)
    # A name another entity still holds is not "deleted".
    tool.post(
        "/entities/e0000/claims/copy",
        {"name": "David Shield", "claim_ids": [DS1], "target_entity_id": "e0002"},
    )
    tool.delete("/entities/e0002")
    assert tool.saved()["deleted_names"] == ["Tel Aviv"]


def test_delete_claim_removes_it_everywhere(tool: EntitiesSetup) -> None:
    tool.post("/aggregations", {"claim_ids": [DS1, DS2], "representative": DS1})
    answer = tool.delete(f"/claims/{DS2}")
    assert answer["summary"]["deleted_claims"] == 1 and answer["summary"]["aggregations"] == 0
    assert DS2 not in tool.shown_claims("e0000", "David Shield")
    assert DS2 not in tool.shown_claims("e0001", "ORM")
    saved = tool.saved()
    consistent(saved)
    assert saved["deleted_claims"] == [DS2] and DS2 not in saved["claim_entities"]
    aggregations = json.loads(tool.aggregations.read_text(encoding="utf-8"))
    assert aggregations["aggregations"] == []  # a group left with one claim dissolves
    tool.delete(f"/claims/{DS2}", expect=409)
    tool.delete("/claims/does-not-exist", expect=404)


def test_manual_aggregation_folds_claims_and_can_be_edited(tool: EntitiesSetup) -> None:
    tool.post("/aggregations", {"claim_ids": [DS1], "representative": DS1}, expect=409)
    tool.post("/aggregations", {"claim_ids": [DS1, DS2], "representative": DS5}, expect=409)
    created = tool.post("/aggregations", {"claim_ids": [DS1, DS2, DS6], "representative": DS2})
    group = created["aggregation"]
    assert tool.shown_claims("e0000", "David Shield") == [DS2, DS5]
    rep = next(
        c for c in tool.member("e0000", "David Shield")["claims"]["claims"] if c["claim_id"] == DS2
    )
    assert rep["aggregation"]["count"] == 3
    tool.post("/aggregations", {"claim_ids": [DS1, DS5], "representative": DS1}, expect=409)

    tool.post(f"/aggregations/{group['id']}/decouple", {"claim_id": DS6})
    assert tool.shown_claims("e0000", "David Shield") == [DS2, DS5, DS6]
    tool.post(f"/aggregations/{group['id']}/representative", {"claim_id": DS1})
    assert tool.shown_claims("e0000", "David Shield") == [DS1, DS5, DS6]
    answer = tool.post(f"/aggregations/{group['id']}/decouple", {"claim_id": DS2})
    assert answer["aggregation"] is None  # one claim left: dissolved
    assert tool.shown_claims("e0000", "David Shield") == [DS1, DS2, DS5, DS6]
    saved = json.loads(tool.aggregations.read_text(encoding="utf-8"))
    assert saved["aggregations"] == [] and saved["metadata"]["count"] == 0
    second = tool.post("/aggregations", {"claim_ids": [DS3, DS4], "representative": DS4})
    tool.delete(f"/aggregations/{second['aggregation']['id']}")
    tool.delete(f"/aggregations/{second['aggregation']['id']}", expect=404)


def test_old_members_without_claim_lists_are_made_explicit(tmp_path: Path) -> None:
    setup = EntitiesSetup(tmp_path, extra_claims=[claim("t-Q-0001", "ORM again", ["ORM"])])
    payload = json.loads(setup.original.read_text(encoding="utf-8"))
    payload["entities"][1]["members"][0]["claim_ids"] = None  # a v1 "every claim naming it"
    payload["entities"][1]["members"][0]["count"] = 0
    setup.original.write_text(json.dumps(payload), encoding="utf-8")
    assert setup.member("e0001", "ORM")["claims"]["count"] == 4
    setup.post("/entities/e0001/claims/exclude", {"name": "ORM", "claim_ids": [DS5]})
    saved = setup.saved_entity("e0001")["members"][0]
    assert saved["claim_ids"] == [DS2, DS4, "t-Q-0001"] and saved["count"] == 3
