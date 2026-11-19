"""Step 4 with the model stages, end to end on fakes: the judge's groups applied, a seed entry
kept whole, a name with two meanings divided per claim, the update path, failures, the
estimate and the cache. Every name is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.tools.entities.store import EntityStore, EntityView
from step_4_entities.run import MatchClients
from tests.entities_match_support import (
    FakeClassifier,
    FakeEmbed,
    FakeLLM,
    MatchStep,
    by_keyword,
    claim,
    grouped,
)

THINGS = {
    "Ministry of Health": ["health ministry"], "MoH": ["health ministry"],
    "Nerida Clinic": ["Nerida"], "Nereda Clinic": ["Nereda"],
    "Dana Levi": ["lawyer"], "Dana": ["lawyer", "insurer"],
}
SEED = [{"canonical": "Dana Levi", "aliases": ["Dana"], "type": "lawyer", "status": "accepted"}]
CLAIMS = [
    claim("t1-a", ["Ministry of Health"], "Ministry of Health approved the papers", "2023-01-01"),
    claim("t1-b", ["MoH"], "MoH approved the birth certificate", "2023-02-01"),
    claim("t2-a", ["Nerida Clinic"], "Nerida Clinic did the transfer", "2023-03-01"),
    claim("t2-b", ["Nereda Clinic"], "Nereda Clinic did the blood tests", "2023-04-01"),
    claim("t3-a", ["Dana Levi"], "Levi prepared the court order", "2023-05-01"),
    claim("t4-a", ["Dana"], "Dana handled our court order", "2023-06-01"),
    claim("t5-a", ["Dana"], "Dana insurance covered the newborn", "2023-07-01"),
    claim("t5-b", ["Dana"], "Dana insurance refused the claim", "2023-07-02"),
    claim("t6-a", ["Dana"], "Dana took the insurer to court", "2023-08-01"),
    claim("t7-a", ["Dana"], "Dana is nice", "2023-09-01"),
]
KEYWORDS = {"lawyer": ["court"], "insurer": ["insur"]}


HEALTH = {"Ministry of Health", "MoH", "משרד הבריאות"}


def _same(a: str, b: str) -> float:
    return 0.9 if {a, b} <= HEALTH else 0.01


def _step(tmp_path: Path, *, judge: object = None, seed: list[dict] | None = SEED,
          failing: set[tuple[str, str]] | None = None) -> MatchStep:
    clients = MatchClients(
        embed=FakeEmbed({"MoH": "Ministry of Health", "משרד הבריאות": "Ministry of Health"}),
        classifier=FakeClassifier(_same, failing),
        llm=FakeLLM(judge or grouped(THINGS, unsure=[("Nerida Clinic", "Nereda Clinic")]),
                    by_keyword(KEYWORDS)),
    )
    return MatchStep(tmp_path, clients, seed)


def test_the_judge_groups_are_applied(tmp_path: Path) -> None:
    step = _step(tmp_path)
    stats = step.run(CLAIMS)
    ministry = step.entity_of("MoH")
    assert sorted(ministry["aliases"]) == ["Ministry of Health", "MoH"]
    assert "model" in ministry["merge_signals"]
    # look-alikes spelling would merge, kept apart by the judge and flagged as unsure
    gaia, gala = step.entity_of("Nerida Clinic"), step.entity_of("Nereda Clinic")
    assert gaia is not gala and gaia["aliases"] == ["Nerida Clinic"]
    assert gaia["status"] == "ambiguous" and gala["canonical_name"] in gaia["conflict_with"]
    assert stats["unjudged_names"] == 0
    matches = step.matches()
    assert {"names": ["Nereda Clinic", "Nerida Clinic"], "reason": "not clear",
            "source": "judge"} in matches["unsure"]
    assert matches["metadata"]["counts"]["bundles"] >= 3
    assert matches["metadata"]["prompt_versions"] == {"screen": 1, "judge": 1, "senses": 1}


def test_a_seed_alias_with_two_meanings_is_divided_per_claim(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.run(CLAIMS)
    lawyer, insurer = sorted(step.entities_of("Dana"), key=lambda e: len(e["members"]),
                             reverse=True)
    # the seed entry stays whole, holding the lawyer's claims; the model took claims of its
    # alias away, so it is not adopted as accepted: the owner checks it
    assert sorted(lawyer["aliases"]) == ["Dana", "Dana Levi"]
    assert lawyer["canonical_name"] == "Dana Levi" and lawyer["status"] == "ambiguous"
    assert lawyer["type"] == "lawyer"
    # the second thing has no other name: an entity of its own, not taken for the seed entry
    assert insurer["aliases"] == ["Dana"] and insurer.get("type") is None
    assert insurer["status"] == "ambiguous"
    assert "Dana" in lawyer["conflict_with"] and "Dana Levi" in insurer["conflict_with"]
    lawyer_claims = set(step.member(lawyer, "Dana")["claim_ids"])
    insurer_claims = set(step.member(insurer, "Dana")["claim_ids"])
    # a claim about both things is in both; the unclear one goes to the bigger meaning
    assert lawyer_claims & insurer_claims == {"t6-a"}
    assert {"t4-a", "t6-a"} <= lawyer_claims and {"t5-a", "t5-b", "t6-a"} <= insurer_claims
    assert ("t7-a" in insurer_claims) != ("t7-a" in lawyer_claims)
    index = step.edited()["claim_entities"]
    assert sorted(index["t6-a"]) == sorted([lawyer["entity_id"], insurer["entity_id"]])
    sense = step.matches()["senses"][0]
    assert sense["name"] == "Dana" and sense["unclear"] == ["t7-a"] and not sense["failed"]
    # the Entities page links each of the two to the other, and shows the name in both
    store = EntityStore(step.artifact.original, step.artifact.edited, step.claims_path,
                        step.claims_path.with_name("claims_edited.json"), step.analysis_path)
    detail = store.detail(lawyer["entity_id"], EntityView())
    assert [c["entity_id"] for c in detail["entity"]["conflicts"]] == [insurer["entity_id"]]


def test_the_shared_name_is_not_merged_back_and_a_new_claim_is_asked(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.run(CLAIMS)
    calls = len(step.clients.llm.calls)
    step.run([*CLAIMS, claim("t8-a", ["Dana"], "Dana insurance raised the price", "2024-01-01"),
              claim("t8-b", ["Dana"], "Dana won in court again", "2024-01-02")])
    lawyer, insurer = sorted(step.entities_of("Dana"), key=lambda e: len(e["members"]),
                             reverse=True)
    assert "t8-a" in step.member(insurer, "Dana")["claim_ids"]
    assert "t8-b" in step.member(lawyer, "Dana")["claim_ids"]
    assert len(step.clients.llm.calls) == calls + 1  # one senses call, no judge call


def test_an_update_attaches_a_new_name_to_the_entity_holding_its_match(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.run(CLAIMS)
    edited = step.edited()
    ministry = next(e for e in edited["entities"] if "MoH" in e["aliases"])
    ministry["canonical_name"], ministry["canonical_manual"] = "Health Ministry (IL)", True
    step.artifact.edited.write_text(json.dumps(edited), encoding="utf-8")
    things = {**THINGS, "משרד הבריאות": ["health ministry"]}
    step.clients.llm.judge = grouped(things)
    step.run([*CLAIMS, claim("t9-a", ["משרד הבריאות"], "משרד הבריאות אישר", "2024-02-01")])
    entity = step.entity_of("משרד הבריאות")
    assert entity["entity_id"] == ministry["entity_id"]
    assert entity["canonical_name"] == "Health Ministry (IL)"  # the reviewer's edit survives
    attached = step.member(entity, "משרד הבריאות")["attached_by"]
    assert attached["signals"] == ["model"] and attached["matched"] in ("MoH", "Ministry of Health")


def test_a_failed_judge_call_leaves_the_spelling_rules_and_a_flag(tmp_path: Path) -> None:
    step = _step(tmp_path, judge=lambda ids: None, seed=None)
    stats = step.run(CLAIMS[:4])
    assert stats["unjudged_names"] == 4
    gaia = step.entity_of("Nerida Clinic")
    assert gaia["status"] == "ambiguous"
    assert {e["why"] for e in step.matches()["unjudged"]} == {"judge"}


def test_an_unscreened_pair_is_flagged_not_dropped(tmp_path: Path) -> None:
    step = _step(tmp_path, seed=None, failing={("Ministry of Health", "MoH")})
    step.run(CLAIMS[:2])
    moh, ministry = step.entity_of("MoH"), step.entity_of("Ministry of Health")
    assert moh is not ministry and moh["status"] == "ambiguous"
    assert step.matches()["unsure"][0]["source"] == "unscreened"


def test_a_second_run_makes_no_paid_call_and_the_estimate_says_so(tmp_path: Path) -> None:
    step = _step(tmp_path)
    before = step.run(CLAIMS, estimate=True)
    assert before["estimate"] and not step.artifact.edited.exists()
    assert not step.clients.llm.calls and not step.clients.classifier.calls
    assert before["paid_calls"][0] >= 2 and before["paid_calls"][1] >= 3 and before["usd"][1] > 0
    assert before["stages"]["screen"]["calls"][0] >= 1
    step.run(CLAIMS)
    calls = (len(step.clients.llm.calls), step.clients.classifier.calls)
    again = step.run(CLAIMS, estimate=True, rebuild=True)
    assert again["paid_calls"] == [0, 0] and again["usd"] == [0.0, 0.0]
    first = step.edited()
    step.run(CLAIMS, rebuild=True)
    assert (len(step.clients.llm.calls), step.clients.classifier.calls) == calls
    assert [e["aliases"] for e in step.edited()["entities"]] == [
        e["aliases"] for e in first["entities"]]
    nothing = step.run(CLAIMS, estimate=True)
    assert nothing["paid_calls"] == [0, 0]


def test_no_match_is_the_spelling_step_and_never_builds_a_client(tmp_path: Path) -> None:
    class Refuses:
        def __getattr__(self, name: str) -> object:
            raise AssertionError("a model client was used")

    (tmp_path / "a").mkdir()
    spelling = MatchStep(tmp_path / "a", MatchClients(Refuses(), Refuses(), Refuses()), SEED)
    spelling.run(CLAIMS, match=False)
    entities = spelling.edited()["entities"]
    assert all("model" not in e["merge_signals"] for e in entities)
    assert not spelling.matches_path.exists()
    dana = [e for e in entities if "Dana" in e["aliases"]]
    assert len(dana) == 1 and sorted(dana[0]["aliases"]) == ["Dana", "Dana Levi"]


@pytest.mark.parametrize("cap", [2])
def test_an_oversized_bundle_records_its_cut_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   cap: int) -> None:
    from step_4_entities.match_bundles import JUDGE_CONFIG

    monkeypatch.setitem(JUDGE_CONFIG, "max_bundle_names", cap)
    claims = [claim("t1-a", ["Orion"], "Orion agency"), claim("t1-b", ["Orion Agency"], "agency"),
              claim("t1-c", ["Orion Agency Ltd"], "agency ltd")]
    step = _step(tmp_path, seed=None, judge=grouped({}))
    step.run(claims)
    cut = [u for u in step.matches()["unsure"] if u["source"] == "cut"]
    assert cut and all(len({id(e) for n in u["names"] for e in step.entities_of(n)}) == 2
                       for u in cut)



@pytest.mark.parametrize(("probability", "recorded"), [(0.2, False), (0.6, True)])
def test_a_weak_cut_pair_is_flagged_only_from_the_bar_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probability: float, recorded: bool
) -> None:
    from step_4_entities.match_bundles import JUDGE_CONFIG

    monkeypatch.setitem(JUDGE_CONFIG, "max_bundle_names", 2)
    monkeypatch.setitem(JUDGE_CONFIG, "cut_unsure_probability", 0.3)
    claims = [claim("t1-a", ["Orion"], "Orion agency"), claim("t1-b", ["Orion Agency"], "agency"),
              claim("t1-c", ["Vesper"], "a clinic")]
    clients = MatchClients(embed=FakeEmbed(), classifier=FakeClassifier(lambda a, b: probability),
                           llm=FakeLLM(grouped({}), by_keyword({})))
    step = MatchStep(tmp_path, clients)
    stats = step.run(claims)
    cut = [u["names"] for u in step.matches()["unsure"] if u["source"] == "cut"]
    assert any("Vesper" in names for names in cut) is recorded
    assert len(step.entities_of("Vesper")) == 1 and stats["entity_count"] >= 2


def test_a_failed_senses_call_keeps_the_claims_together_and_flags(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.clients.llm.senses = lambda user: None
    step.run(CLAIMS)
    lawyer = step.entity_of("Dana")
    assert len(step.member(lawyer, "Dana")["claim_ids"]) == 5
    assert step.matches()["senses"][0]["failed"]
    # the claims stay in the seed entry, which the owner is asked to check
    assert lawyer is not None and "Dana Levi" in lawyer["aliases"]
    assert lawyer["status"] == "ambiguous"
