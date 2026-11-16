"""Step 4's model stages, piece by piece: the net, the screen, the bundles, the judge's answer.
Fakes only (``entities_match_support``); every name is invented."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from step_4_entities.cluster import EntityClusterer
from step_4_entities.match_bundles import build_bundles
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_judge import Group, Judge, resolve_groups
from step_4_entities.match_net import (
    NET_CONFIG,
    SOURCE_MEANING_CLAIMS,
    SOURCE_MEANING_NAME,
    SOURCE_PREFIX,
    SOURCE_SPELLING,
    CandidateNet,
)
from step_4_entities.match_screen import PairScreen
from step_4_entities.match_senses import SENSES_CONFIG, SenseJob, SenseSplitter
from tests.entities_match_support import FakeClassifier, FakeEmbed, FakeLLM, claim, grouped
from tests.entities_fixtures import collect_entities

LOGGER = logging.getLogger("test")

CLAIMS = [
    claim("t1-a", ["Nerida Clinic"], "Nerida Clinic did the transfer", "2023-01-05"),
    claim("t1-b", ["Nereda Clinic"], "Nereda Clinic did the tests", "2023-02-05"),
    claim("t2-a", ["Ministry of Health"], "Ministry of Health approved the papers", "2023-03-01"),
    claim("t2-b", ["MoH"], "MoH approved the papers", "2023-04-01"),
    claim("t3-a", ["Orion"], "Orion agency helped us", "2023-05-01"),
    claim("t3-b", ["Orion Agency"], "Orion Agency charges a fee", "2023-06-01"),
    claim("t4-a", ["Zephyr Bank"], "Zephyr Bank opened an account", "2023-07-01"),
]


def _net(tmp_path: Path, new: set[str] | None = None,
         topics: dict[str, str] | None = None) -> CandidateNet:
    members = collect_entities(CLAIMS)
    clusterer = EntityClusterer({}, similarity_threshold=0.8, seed_path=tmp_path / "none.json")
    names = new if new is not None else {m["name"] for m in members}
    return CandidateNet(members, names, clusterer.index(members),
                        ClaimSampler({c["claim_id"]: c for c in CLAIMS}),
                        FakeEmbed(topics or {"MoH": "Ministry of Health"}))


def test_the_net_holds_spelling_and_meaning_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(NET_CONFIG, "neighbours", 1)
    pairs = _net(tmp_path).pairs()
    look_alike = pairs[("Nereda Clinic", "Nerida Clinic")]
    assert SOURCE_SPELLING in look_alike.sources and look_alike.strong
    prefix = pairs[("Orion", "Orion Agency")]
    assert SOURCE_PREFIX in prefix.sources and prefix.strong
    meaning = pairs[("Ministry of Health", "MoH")]
    assert {SOURCE_MEANING_NAME, SOURCE_MEANING_CLAIMS} <= meaning.sources
    assert not meaning.strong
    # one neighbour per name and source: the net stays small
    assert all(len(c.sources & {SOURCE_MEANING_NAME}) <= 1 for c in pairs.values())
    assert len(pairs) <= 7 * 2 + 2


def test_the_net_ranks_neighbours_without_a_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(NET_CONFIG, "neighbours", 3)
    pairs = _net(tmp_path, new={"Zephyr Bank"}).pairs()
    meaning = [p for p, c in pairs.items() if SOURCE_MEANING_NAME in c.sources]
    assert len(meaning) == 3 and all("Zephyr Bank" in p for p in meaning)
    # only the new name gets partners: no pair of two existing names
    assert all("Zephyr Bank" in pair for pair in pairs)


def test_strong_pairs_skip_the_screen_and_weak_ones_below_the_bar_drop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(NET_CONFIG, "neighbours", 1)
    pairs = _net(tmp_path).pairs()
    classifier = FakeClassifier(lambda a, b: 0.9 if {a, b} == {"Ministry of Health", "MoH"} else 0.01)
    weak = [p for p, c in pairs.items() if not c.strong]
    result = PairScreen(classifier, ClaimSampler({c["claim_id"]: c for c in CLAIMS}),
                        {c["entities"][0]: [c["claim_id"]] for c in CLAIMS}, LOGGER).screen(weak)
    asked = {FakeClassifier.names(state) for state in classifier.states}
    assert ("Nereda Clinic", "Nerida Clinic") not in asked and ("Orion", "Orion Agency") not in asked
    assert result.probabilities[("Ministry of Health", "MoH")] == 0.9
    assert all("Name A: " in s and "\n- 2023-" in s for s in classifier.states)


def test_a_failed_screen_pair_is_reported_not_dropped(tmp_path: Path) -> None:
    classifier = FakeClassifier(lambda a, b: 0.5, failing={("A", "B")})
    screen = PairScreen(classifier, ClaimSampler({}), {}, LOGGER)
    result = screen.screen([("A", "B"), ("A", "C")])
    assert result.failed == [("A", "B")] and result.probabilities == {("A", "C"): 0.5}


def test_an_oversized_bundle_is_cut_at_its_weakest_links_and_drops_no_pair() -> None:
    names = ["a", "b", "c", "d", "e", "f"]
    edges = {("a", "b"): 0.9, ("b", "c"): 0.8, ("c", "d"): 0.1, ("d", "e"): 0.95, ("e", "f"): 0.7}
    bundles, cut = build_bundles(names, edges, units=[["e", "f"]], cap=3)
    assert [b.names for b in bundles] == [["a", "b", "c"], ["d", "e", "f"]]
    assert cut == [("c", "d")]
    inside = {p for p in edges for b in bundles if set(p) <= set(b.names)}
    assert inside | set(cut) == set(edges)
    assert bundles[1].units == [["e", "f"]]
    again, _ = build_bundles(names, edges, units=[["e", "f"]], cap=3)
    assert [b.bundle_id for b in again] == [b.bundle_id for b in bundles]


def test_a_fixed_unit_joins_a_bundle_whole_and_a_lone_name_is_no_bundle() -> None:
    bundles, cut = build_bundles(["a", "b", "c", "x"], {("a", "b"): 0.5}, units=[["b", "c"]])
    assert [b.names for b in bundles] == [["a", "b", "c"]] and cut == []


def test_resolve_keeps_a_seed_unit_whole_but_lets_a_shared_alias_keep_its_second_meaning() -> None:
    unit = [["Dana", "Dana Levi"]]
    # the alias listed twice: its seed entry is completed, its second meaning stays apart
    groups = [Group("a lawyer", ["Dana Levi", "Levi Law"]), Group("an insurer", ["Dana"]),
              Group("a lawyer too", ["Dana"])]
    resolved = resolve_groups(groups, unit)
    assert [g.names for g in resolved] == [["Dana", "Dana Levi", "Levi Law"], ["Dana"], ["Dana"]]
    # listed once, apart from its entry: the seed wins and the groups join
    joined = resolve_groups([Group("a lawyer", ["Dana Levi", "Levi Law"]),
                             Group("an insurer", ["Dana"])], unit)
    assert [g.names for g in joined] == [["Dana", "Dana Levi", "Levi Law"]]
    split = resolve_groups([Group("x", ["Dana Levi"]), Group("y", ["Levi Law", "Lev"])],
                           [["Dana Levi", "Levi Law"]])
    assert [g.names for g in split] == [["Dana Levi", "Lev", "Levi Law"]]


def test_the_judge_prompt_lists_units_and_spread_claims_and_its_answer_is_read() -> None:
    claims = [claim(f"t{i}-a", ["Dana"], f"claim number {i}", f"2023-{i:02d}-01")
              for i in range(1, 13)]
    claims.append(claim("t20-a", ["Dana Levi"], "the lawyer", "2023-01-01"))
    claims_of = {"Dana": [c["claim_id"] for c in claims[:12]], "Dana Levi": ["t20-a"]}
    sampler = ClaimSampler({c["claim_id"]: c for c in claims})
    llm = FakeLLM(grouped({"Dana": ["lawyer", "insurer"], "Dana Levi": ["lawyer"]},
                          unsure=[("Dana", "Dana Levi")]))
    judge = Judge(llm, sampler, claims_of, LOGGER)
    bundles, _ = build_bundles(["Dana", "Dana Levi"], {}, units=[["Dana", "Dana Levi"]])
    user = str(judge.request(bundles[0]).user)
    assert 'N1 "Dana" | 12 claims' in user and "U1: N1, N2" in user
    shown = [line for line in user.splitlines() if line.startswith("- 2023")]
    assert shown[0].startswith("- 2023-01") and any(s.startswith("- 2023-12") for s in shown)
    assert len(shown) == 4 + 1  # sqrt(12) rounded up, and the one claim of the other name
    verdict = judge.judge(bundles, batch=True).verdicts[bundles[0].bundle_id]
    assert [g.names for g in verdict.groups] == [["Dana", "Dana Levi"], ["Dana"]]
    assert verdict.shared() == {"Dana"} and verdict.unsure[0][0] == ("Dana", "Dana Levi")


def test_a_judge_answer_without_groups_is_a_failure() -> None:
    llm = FakeLLM(lambda ids: {"groups": [], "unsure": []})
    bundles, _ = build_bundles(["a", "b"], {("a", "b"): 1.0}, units=[])
    result = Judge(llm, ClaimSampler({}), {}, LOGGER).judge(bundles, batch=True)
    assert result.failed == [bundles[0].bundle_id] and not result.verdicts


@pytest.mark.parametrize("per_call", [2, 60])
def test_the_senses_calls_keep_conversations_whole_and_read_several_groups(monkeypatch: pytest.MonkeyPatch,
                                                                           per_call: int) -> None:
    monkeypatch.setitem(SENSES_CONFIG, "claims_per_call", per_call)
    claims = [claim("t1-a", ["Dana"], "Dana won the court case"),
              claim("t1-b", ["Dana"], "Dana court fees"),
              claim("t1-c", ["Other"], "a context line"),
              claim("t2-a", ["Dana"], "Dana insurance paid"),
              claim("t3-a", ["Dana"], "Dana sued Dana insurance in court")]
    by_id = {c["claim_id"]: c for c in claims}
    splitter = SenseSplitter(FakeLLM(lambda ids: None), ClaimSampler(by_id), by_id, LOGGER)
    job = SenseJob("Dana", ("t1-a", "t1-b", "t2-a", "t3-a"),
                   (("a lawyer", ("Dana", "Dana Levi")), ("an insurer", ("Dana",))))
    requests = splitter.requests(job)
    first = str(requests[0][0].user)
    assert "context: 2024-01: a context line" in first and requests[0][1][:2] == ["t1-a", "t1-b"]
    assert len(requests) == (2 if per_call == 2 else 1)
    answer = {"claims": [{"claim": "C1", "groups": ["G1", "G2"]}, {"claim": "C2", "groups": ["G9"]}]}
    assert SenseSplitter.parse(answer, ["x", "y", "z"], 2) == {"x": [0, 1], "y": [], "z": []}
