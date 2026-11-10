"""The claim-groups tool, reading side: registration, missing files, list, detail, suggestions,
following the files on disk, the cache-only embedder (synthetic C6 via a real step-5 run)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from review_tool.artifacts import GRAPH
from review_tool.tools import TOOL_SPECS
from step_5_aggregate.reviewer.suggestions import CachedEmbedder, EmbeddingsMissing
from tests.groups_tool_support import GroupsSetup
from utils.embedder import Embedder


@pytest.fixture
def groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GroupsSetup:
    return GroupsSetup(tmp_path, monkeypatch)


def test_tool_is_hosted_by_the_server() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "groups")
    assert spec.reads == ("groups", "claims", "entities") and spec.writes == ("groups",)
    assert GRAPH.node("groups").step == "step_5_aggregate"


def test_missing_files_register_and_answer_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    groups = GroupsSetup(tmp_path, monkeypatch, run_step=False)
    tools = groups.client.get("/api/tools").json()
    assert tools[0]["name"] == "groups" and tools[0]["missing"] == ["groups", "claims", "entities"]
    for path in ("/summary", "/groups?limit=5", "/groups/g-1", "/suggestions?limit=5"):
        response = groups.client.get(f"/api/groups{path}")
        assert response.status_code == 409 and "שלב 5" in response.json()["detail"]
    groups.run_step()  # step 5 ran: picked up without a restart
    assert groups.get("/summary")["group_count"] == 9


def test_summary_counts_and_reading_never_creates_the_edited_file(groups: GroupsSetup) -> None:
    groups.edited.unlink()  # as if the owner never edited: a run writes both files
    summary = groups.get("/summary")
    assert summary["file"] == "claims_aggregated.json" and not summary["edited_exists"]
    assert (summary["group_count"], summary["multi_count"], summary["claim_count"]) == (9, 1, 11)
    assert summary["contradiction_groups"] == 2 and summary["not_same_count"] == 0
    assert summary["suggestions"]["total"] == 2 and summary["suggestions"]["available"]
    assert [(e["name"], e["groups"]) for e in summary["entities"]] == [
        ("Beta", 2),
        ("Gamma", 2),
        ("Alpha", 1),
    ]
    groups.get("/groups", limit=50)
    groups.get(f"/groups/{groups.gid('a1')}")
    groups.get("/suggestions", limit=50)
    assert not groups.edited.exists()


def test_list_filters_sorts_and_pages(groups: GroupsSetup) -> None:
    page = groups.get("/groups", limit=2)
    assert page["total"] == 9 and len(page["items"]) == 2
    assert page["items"][0]["group_id"] == groups.gid("a1") and page["items"][0]["size"] == 3
    assert groups.get("/groups", limit=50, multi=True)["total"] == 1
    contra = groups.get("/groups", limit=50, contradictions=True)["items"]
    assert {i["group_id"] for i in contra} == {groups.gid("o1"), groups.gid("o2")}
    assert groups.get("/groups", limit=50, suggested=True)["total"] == 4
    alpha = groups.get("/summary")["entities"][-1]["entity_id"]
    assert groups.get("/groups", limit=50, entity=alpha)["total"] == 1
    assert groups.get("/groups", limit=50, stance="negative")["items"][0]["group_id"] == groups.gid("o2")
    assert groups.get("/groups", limit=50, q="CLAIM DB")["items"][0]["group_id"] == groups.gid("d1")
    by_date = groups.get("/groups", limit=50, sort="last_date", order="asc")["items"]
    assert by_date[0]["group_id"] == groups.gid("s1")
    created = groups.get("/groups", limit=50, sort="created", order="asc")["items"]
    assert created[0]["group_id"] == groups.gid("a1")
    second = groups.get("/groups", limit=2, offset=2)
    assert second["offset"] == 2 and second["items"][0]["group_id"] not in {i["group_id"] for i in page["items"]}
    assert groups.client.get("/api/groups/groups?limit=100000").status_code == 422


def test_detail_shows_members_people_timeline_and_links(groups: GroupsSetup) -> None:
    gid = groups.gid("a1")
    answer = groups.get(f"/groups/{gid}", multi=True)
    group = answer["group"]
    assert answer["queue"] == {"position": 0, "total": 1, "prev_id": None, "next_id": None}
    assert [m["claim_id"] for m in group["members"]] == ["a1", "a2", "a3"]
    assert group["members"][1]["supporter_count"] == 1 and group["members"][1]["stance"] == "factual"
    # p1 backed a1 and later disputed a2: counted once, as an opposer.
    assert (group["supporter_count"], group["opposer_count"]) == (1, 1)
    assert group["timeline"] == [
        {"month": "2024-01", "supporters": 1, "opposers": 0},
        {"month": "2024-02", "supporters": 1, "opposers": 0},
        {"month": "2024-03", "supporters": 0, "opposers": 1},
    ]
    assert group["entities"][0]["name"] == "Alpha"
    assert sum(m["is_representative"] for m in group["members"]) == 1
    o1 = groups.get(f"/groups/{groups.gid('o1')}")["group"]
    assert [link["group_id"] for link in o1["contradicts"]] == [groups.gid("o2")]
    assert o1["contradicts"][0]["manual"] is False and o1["contradicts"][0]["claim_text"]
    outside = groups.get(f"/groups/{groups.gid('c1')}", multi=True)
    assert outside["queue"]["position"] is None
    missing = groups.client.get("/api/groups/groups/g-nothing")
    assert missing.status_code == 404 and "g-nothing" in missing.json()["detail"]


def test_suggestions_are_ranked_and_carry_both_sides(groups: GroupsSetup) -> None:
    answer = groups.get("/suggestions", limit=10)
    assert answer["total"] == 2 and answer["available"] and answer["missing_embeddings"] == 0
    assert answer["bars"] == {"shared_entity": 0.95, "no_shared_entity": 0.96}
    first, second = answer["items"]
    assert (first["a"]["group_id"], first["b"]["group_id"]) == (groups.gid("s1"), groups.gid("s2"))
    assert first["margin"] == pytest.approx(-0.02, abs=1e-3) and first["shared_entity"]
    assert first["key"] == f"{groups.gid('s1')}|{groups.gid('s2')}"
    assert first["a"]["members"][0]["claim_text"] and first["a"]["supporter_count"] == 1
    assert (second["a"]["group_id"], second["b"]["group_id"]) == (groups.gid("t1"), groups.gid("t2"))
    assert second["margin"] == pytest.approx(-0.025, abs=1e-3) and not second["shared_entity"]
    # Opposite stances (o1 / o2) are never suggested, only linked as contradictions.
    assert groups.gid("o1") not in {s[k]["group_id"] for s in answer["items"] for k in ("a", "b")}
    partners = groups.get(f"/groups/{groups.gid('s2')}")["group"]["partners"]
    assert [p["key"] for p in partners] == [first["key"]]


def test_missing_vectors_keep_views_but_block_edits(groups: GroupsSetup) -> None:
    groups.cache_misses(3)
    groups.client = groups.new_client()
    suggestions = groups.get("/suggestions", limit=10)
    assert suggestions == {
        **suggestions,
        "items": [],
        "total": 0,
        "available": False,
        "missing_embeddings": 3,
        "bars": None,
    }
    assert groups.get(f"/groups/{groups.gid('a1')}")["group"]["size"] == 3
    stamp = groups.edited.stat().st_mtime_ns
    refused = groups.client.post("/api/groups/groups/merge", json={"group_ids": [groups.gid("d1"), groups.gid("c1")]})
    assert refused.status_code == 409 and "שלב 5" in refused.json()["detail"]
    assert groups.edited.stat().st_mtime_ns == stamp


def test_step5_rerun_under_the_open_tool_is_picked_up(groups: GroupsSetup) -> None:
    assert groups.get("/summary")["group_count"] == 9
    groups.corpus.add("e1", plane=6, date="2024-08-01")
    assert groups.run_step()["written"]
    assert groups.get("/summary")["group_count"] == 10
    assert groups.get(f"/groups/{groups.gid('e1')}")["group"]["members"][0]["claim_id"] == "e1"


def test_cached_embedder_never_calls_the_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_request(self: Embedder, texts: list[str], kind: str) -> list[list[float]]:
        calls.append(len(texts))
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]

    monkeypatch.setattr(Embedder, "_request", fake_request)
    with pytest.raises(EmbeddingsMissing) as missing:
        CachedEmbedder(cache_dir=tmp_path).embed_texts(["one", "two"], "similarity")
    assert missing.value.count == 2 and calls == []
    paid = Embedder(cache_dir=tmp_path).embed_texts(["one", "two"], "similarity")
    assert calls == [2]
    cached = CachedEmbedder(cache_dir=tmp_path).embed_texts(["two", "one"], "similarity")
    assert np.allclose(cached, paid[::-1], atol=1e-3) and calls == [2]
