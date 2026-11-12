"""The entity review tool in the frame: endpoints, persistence, reloads, locks (synthetic data)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.artifacts import GRAPH
from review_tool.tools import TOOL_SPECS
from step_4_entities.mentions import SimpleAnalyzer
from step_4_entities.run import run
from tests.entities_tool_support import (
    DS1,
    DS2,
    DS5,
    DS6,
    EntitiesSetup,
    claim,
    claims_payload,
)
from tests.review_tool_support import locked_elsewhere
from utils.json_io import write_json_file
from utils.paths import Artifact


@pytest.fixture
def tool(tmp_path: Path) -> EntitiesSetup:
    return EntitiesSetup(tmp_path)


# ------------------------------------------------------------- registration and frame


def test_tool_is_hosted_by_the_server() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "entities")
    assert spec.reads == ("claims", "entities")
    assert spec.writes == ("entities", "claim_aggregations")
    assert GRAPH.node("claim_aggregations").original.name == "claim_aggregations.json"


def test_missing_files_still_register_and_answer_409(tmp_path: Path) -> None:
    setup = EntitiesSetup(tmp_path, with_claims=False, with_entities=False, with_cache=False)
    tools = setup.client.get("/api/tools").json()
    assert tools[0]["name"] == "entities" and tools[0]["title"] == "Entities"
    assert tools[0]["missing"] == ["claims", "entities"]  # the aggregations file is optional
    for path in ("/summary", "/entities?limit=5", "/entities/e0000", "/deleted-names"):
        response = setup.client.get(f"/api/entities{path}")
        assert response.status_code == 409, path
        assert "run step" in response.json()["detail"]
    assert not setup.edited.exists()


def test_reads_never_create_the_edited_file(tool: EntitiesSetup) -> None:
    tool.get("/summary")
    tool.get("/entities", limit=10)
    tool.get("/entities/e0000")
    tool.get("/entities/e0000/claims", name="David Shield")
    tool.get("/deleted-names")
    assert not tool.edited.exists() and not tool.aggregations.exists()
    assert tool.get("/summary")["file"] == "entities.json"


def test_a_locked_file_answers_423_and_changes_nothing(tool: EntitiesSetup) -> None:
    with locked_elsewhere(tool.edited):
        response = tool.client.post(
            "/api/entities/entities/e0000/rename", json={"canonical_name": "X"}
        )
    assert response.status_code == 423
    assert not tool.edited.exists()
    assert tool.entity("e0000")["canonical_name"] == "David Shield"


def test_unknown_entity_is_404_and_an_impossible_change_is_409(tool: EntitiesSetup) -> None:
    assert "e9999" in tool.get("/entities/e9999", expect=404)["detail"]
    tool.post("/entities/e0000/merge", {"target_entity_id": "e0000"}, expect=409)
    tool.post("/entities/e0000/rename", {"canonical_name": "  "}, expect=409)
    tool.post("/entities/e0000/claims/exclude", {"name": "Nobody", "claim_ids": [DS1]}, expect=404)
    bogus = {"kind": "email", "value": "x", "action": "bogus"}
    tool.post("/entities/e0001/contact", bogus, expect=422)
    assert not tool.edited.exists()


def test_an_entity_has_no_kind_to_set_or_read(tool: EntitiesSetup) -> None:
    response = tool.client.post("/api/entities/entities/e0000/kind", json={"kind": "person"})
    assert response.status_code in (404, 405)
    assert not tool.edited.exists()
    assert not {key for key in tool.entity("e0000") if key.startswith("kind")}
    assert not any(key.startswith("kind") for key in tool.get("/entities/e0000")["entity"])


# ------------------------------------------------------------- reads


def test_summary_counts_what_needs_review(tool: EntitiesSetup) -> None:
    summary = tool.get("/summary")
    assert summary["entity_count"] == 3 and summary["multi_member_count"] == 1
    assert summary["by_status"] == {"suggested": 1, "accepted": 1, "ambiguous": 1}
    assert (summary["attached_members"], summary["attached_entities"]) == (1, 1)
    assert summary["conflict_entities"] == 1
    assert summary["uncertain_entities"] == 2  # ORM and Tel Aviv share one address
    assert summary["analysis"] == {"cached": 6, "fallback": 0}
    assert not summary["edited_exists"]


def test_without_the_analysis_cache_the_model_free_analyzer_is_used(tmp_path: Path) -> None:
    setup = EntitiesSetup(tmp_path, with_cache=False)
    assert setup.get("/summary")["analysis"] == {"cached": 0, "fallback": 6}
    assert setup.shown_claims("e0000", "David Shield") == [DS1, DS2, DS5, DS6]
    assert not setup.cache.exists()  # the server never writes step 4's cache


def test_list_filters_sorts_searches_and_pages(tool: EntitiesSetup) -> None:
    def ids(**params: object) -> list[str]:
        return [item["entity_id"] for item in tool.get("/entities", limit=10, **params)["items"]]

    # Needs review first (a name attached by an update, a look-alike), then by size; decided last.
    assert ids() == ["e0000", "e0002", "e0001"]
    sections = [item["section"] for item in tool.get("/entities", limit=10)["items"]]
    assert sections == ["review", "review", "accepted"]
    assert ids(sort="count") == ["e0000", "e0001", "e0002"]  # most claims first
    assert ids(sort="id", order="desc") == ["e0002", "e0001", "e0000"]
    assert ids(status="ambiguous") == ["e0002"]
    assert ids(flag="attached") == ["e0000"]
    assert ids(flag="conflict") == ["e0002"]
    assert ids(flag="uncertain", sort="count") == ["e0001", "e0002"]
    assert ids(flag="multi") == ["e0000"]
    assert ids(q="davidsh") == ["e0000"]  # aliases are searched too
    page = tool.get("/entities", limit=2, offset=2)
    assert (page["total"], [i["entity_id"] for i in page["items"]]) == (3, ["e0001"])
    item = tool.get("/entities", limit=1)["items"][0]
    assert (item["member_count"], item["attached_count"], item["total_count"]) == (2, 1, 5)


def test_detail_shows_members_highlights_and_what_step_4_flagged(tool: EntitiesSetup) -> None:
    answer = tool.get("/entities/e0000", status="all")
    assert answer["queue"] == {"position": 0, "total": 3, "prev_id": None, "next_id": "e0002"}
    entity = answer["entity"]
    attached = next(m for m in entity["members"] if m["name"] == "DavidShield")
    assert attached["attached_by"]["matched"] == "David Shield"
    shown = tool.get("/entities/e0000/claims", name=entity["members"][0]["name"], limit=100)["claims"]
    sample = next(c for c in shown if c["claim_id"] == DS2)
    kinds = {h["kind"] for h in sample["highlights"]}
    assert kinds == {"self", "other"}
    other = next(h for h in sample["highlights"] if h["kind"] == "other")
    assert (other["entity_id"], other["name"]) == ("e0001", "ORM")
    mention_only = next(c for c in shown if c["claim_id"] == DS5)
    assert mention_only["tagged"] is False
    related = next(c for c in shown if c["claim_id"] == DS6)
    assert related["related_entities"] == [
        {
            "name": "Great Morning",
            "entity_id": None,
            "canonical_name": "Great Morning",
            "color_index": None,
            "tagged_only": True,
        }
    ]
    ambiguous = tool.entity("e0002")
    assert ambiguous["conflicts"][0]["entity_id"] == "e0000"


def test_member_claims_are_paged(tmp_path: Path) -> None:
    extra = [claim(f"t-Z-{i:04d}", f"David Shield note {i}", ["David Shield"]) for i in range(14)]
    setup = EntitiesSetup(tmp_path, extra_claims=extra)
    setup.post("/entities/e0000/claims/exclude", {"name": "David Shield", "claim_ids": [DS6]})
    member = setup.member("e0000", "David Shield")
    assert member["claims"]["count"] == 17  # 18 claims name it, one is excluded
    assert len(member["claims"]["claims"]) == 3  # the detail carries the first three
    first = setup.get("/entities/e0000/claims", name="David Shield", offset=0, limit=5)
    second = setup.get("/entities/e0000/claims", name="David Shield", offset=5, limit=5)
    assert (first["count"], len(first["claims"]), len(second["claims"])) == (17, 5, 5)
    assert {c["claim_id"] for c in first["claims"]}.isdisjoint(
        c["claim_id"] for c in second["claims"]
    )
    setup.get("/entities/e0000/claims", name="Nobody", expect=404)


# ------------------------------------------------------------- persistence and reloads


def test_accept_confirms_flags_moves_on_and_persists(tool: EntitiesSetup) -> None:
    view = {"status": "suggested", "flag": "all", "q": "", "sort": "count", "order": "desc"}
    answer = tool.post("/entities/e0000/status", {"status": "accepted", "view": view})
    assert answer["entity_id"] is None  # the suggested view is empty now
    assert answer["summary"]["attached_members"] == 0
    saved = tool.saved_entity("e0000")
    assert saved["status"] == "accepted"
    assert all("attached_by" not in m for m in saved["members"])
    original = json.loads(tool.original.read_text(encoding="utf-8"))
    assert original["entities"][0]["status"] == "suggested"  # the original is never written
    fresh = tool.new_client()
    assert fresh.get("/api/entities/entities/e0000").json()["entity"]["status"] == "accepted"
    by_count = {**view, "status": "all"}
    answer = tool.post("/entities/e0002/status", {"status": "accepted", "view": by_count})
    assert answer["entity_id"] == "e0002"  # last in the "all" view: stays
    assert "conflict_with" not in tool.saved_entity("e0002")


def test_an_external_step_4_rewrite_is_picked_up_before_the_next_change(
    tool: EntitiesSetup,
) -> None:
    tool.post("/entities/e0000/rename", {"canonical_name": "David Shield Ltd"})
    payload = tool.saved()
    payload["entities"].append({**payload["entities"][2], "entity_id": "e0007"})
    payload["entities"][2]["canonical_name"] = "Tel Aviv (step 4)"
    payload["metadata"]["last_entity_number"] = 7
    write_json_file(payload, tool.edited)  # as a step-4 run underneath the open tool would
    assert tool.get("/summary")["entity_count"] == 4
    tool.post("/entities/e0001/members/move", {"name": "ORM", "target_entity_id": None})
    saved = tool.saved()
    names = {e["entity_id"]: e["canonical_name"] for e in saved["entities"]}
    assert names["e0002"] == "Tel Aviv (step 4)" and "e0007" in names
    assert names["e0008"] == "ORM"  # the new id continues after the external high-water mark
    assert saved["metadata"]["last_entity_number"] == 8


def test_edits_survive_a_real_step_4_rerun_and_the_tool_follows_it(tmp_path: Path) -> None:
    setup = EntitiesSetup(tmp_path, with_entities=False)
    artifact = Artifact(setup.original, setup.edited)

    def step_4() -> dict:
        return run(
            claims_path=setup.claims_path,
            artifact=artifact,
            analyzer=SimpleAnalyzer(),
            analysis_path=setup.cache,
            seed_path=tmp_path / "seed.json",
        )

    step_4()
    by_name = {e["canonical_name"]: e["entity_id"] for e in setup.saved()["entities"]}
    shield = by_name["David Shield"]
    setup.post(f"/entities/{shield}/rename", {"canonical_name": "Shield Insurance"})
    setup.delete(f"/entities/{by_name['Tel Aviv']}")
    extra = [claim("t-N-0001", "Tel Aviv branch and David Shield", ["Tel Aviv", "David Shield"])]
    write_json_file(claims_payload(extra), setup.claims_path)
    step_4()
    saved = setup.saved()
    assert next(e for e in saved["entities"] if e["entity_id"] == shield)["canonical_name"] == (
        "Shield Insurance"
    )
    assert "Tel Aviv" in saved["deleted_names"]  # the deleted name did not come back
    assert all(m["name"] != "Tel Aviv" for e in saved["entities"] for m in e["members"])
    assert "t-N-0001" in setup.shown_claims(shield, "David Shield")  # the open tool reloaded


# ------------------------------------------------------------- what a decision rests on


def test_a_look_alike_comes_with_its_names_and_a_few_claims_each(tool: EntitiesSetup) -> None:
    entity = tool.entity("e0002")
    [look_alike] = entity["look_alikes"]
    assert (look_alike["entity_id"], look_alike["canonical_name"]) == ("e0000", "David Shield")
    names = {m["name"]: m["claims"] for m in look_alike["members"]}
    assert set(names) == {"David Shield", "DavidShield"}
    assert names["David Shield"]["count"] == 4 and len(names["David Shield"]["claims"]) == 3
    assert names["David Shield"]["claims"][0]["highlights"]  # the name is marked in the text
    assert tool.entity("e0000")["look_alikes"] == []


def test_each_contact_names_the_claim_it_came_from(tool: EntitiesSetup) -> None:
    sources = {(s["kind"], s["value"]): s for s in tool.entity("e0000")["contact_sources"]}
    email = sources[("email", "joindavidshield@davidshield.com")]
    assert (email["claim_id"], email["name"]) == (DS1, "David Shield")
    assert "travel insurance" in email["claim_text"]
    uncertain = {(s["kind"], s["value"]) for s in tool.entity("e0001")["contact_sources"]}
    assert ("email", "maybe@shared.com") in uncertain


def test_cohesion_is_marked_only_when_low(tool: EntitiesSetup) -> None:
    items = {item["entity_id"]: item for item in tool.get("/entities", limit=10)["items"]}
    assert items["e0001"]["low_cohesion"] is False  # one name: nothing to hold together
    payload = json.loads(tool.original.read_text(encoding="utf-8"))
    payload["entities"][0]["score"] = 0.5
    write_json_file(payload, tool.original)
    assert tool.get("/entities", limit=10)["items"][0]["low_cohesion"] is True


def test_accepting_in_the_review_order_moves_on_to_the_next_to_review(tool: EntitiesSetup) -> None:
    answer = tool.post("/entities/e0000/status", {"status": "accepted"})
    assert answer["entity_id"] == "e0002"  # the one now first in "needs review"
    sections = {item["entity_id"]: item["section"] for item in tool.get("/entities", limit=10)["items"]}
    assert sections == {"e0002": "review", "e0000": "accepted", "e0001": "accepted"}
