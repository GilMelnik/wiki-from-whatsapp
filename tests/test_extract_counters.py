"""Step 3 and disputes: a dissent of substance is a claim that ``counters`` the claim it
answers, in fresh and update mode; invalid references are dropped; the link is mutual; the
consistency check; the scrub; the cache key. Fake model, synthetic data only.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from step_3_extract.claims import OutputError, ThreadClaims, check_counters, claim_id_for
from step_3_extract.prompt import EXTRACT_SYSTEM, FRESH_SCHEMA, UPDATE_SCHEMA
from tests.test_extract_run import DAN, Corpus, FakeLLM, _message
from utils.llm_client import LLMClient


def _thread() -> dict:
    return {
        "thread_id": "t-A",
        "messages": [
            {"id": wa_id, "datetime": f"2024-01-0{n}T10:00:00", "sender": f"p{n}",
             "content": f"message {n}", "quote": None, "reactions": []}
            for n, wa_id in enumerate(("A", "B", "C", "D"), start=1)
        ],
    }


def _claim(text: str, supporting: list[int], opposing: list[int] = (), counters: list[int] = (),
           *, stance: str = "positive", **extra: list[int]) -> dict:
    return {"text": text, "entities": ["Alpha"], "stance": stance, "supporting": supporting,
            "opposing": list(opposing), "counters": list(counters), **extra}


def _existing(claim_id: str, supporting: list[str], opposing: list[str] = ()) -> dict:
    """An existing claim record as an older step-3 run wrote it (no ``counters`` key yet)."""
    return {"claim_id": claim_id, "thread_id": "t-A", "claim_text": f"text of {claim_id}",
            "entities": [], "stance": "positive", "supporting_message_ids": supporting,
            "opposing_message_ids": list(opposing)}


# ---------------------------------------------------------------- the model's answer -> links

def test_a_dissent_in_the_same_answer_links_both_claims() -> None:
    job = ThreadClaims(_thread())
    answer = {"claims": [_claim("Alpha is good", [1], [2]),
                         _claim("Alpha lost a file", [2], counters=[1], stance="negative"),
                         _claim("Beta is open on Fridays", [3])]}
    good, bad, beta = job.absorb(answer, [])
    assert good["counters"] == [bad["claim_id"]]
    assert bad["counters"] == [good["claim_id"]]
    assert beta["counters"] == []
    assert job.invalid_counters == []


def test_a_link_given_on_both_sides_is_stored_once() -> None:
    job = ThreadClaims(_thread())
    answer = {"claims": [_claim("x", [1], [2], counters=[2]), _claim("y", [2], counters=[1, 1])]}
    x, y = job.absorb(answer, [])
    assert (x["counters"], y["counters"]) == ([y["claim_id"]], [x["claim_id"]])


def test_invalid_references_are_dropped_and_explained() -> None:
    job = ThreadClaims(_thread())
    answer = {"claims": [
        _claim("x", [1], counters=[9, 1, 0]),       # out of range, itself, out of range
        _claim("ungrounded", [42], counters=[1]),   # dropped: no evidence
        _claim("y", [2], counters=[2]),             # refers to the dropped claim
    ]}
    x, y = job.absorb(answer, [])
    assert x["counters"] == [] and y["counters"] == []
    assert len(job.invalid_counters) == 5
    assert any("cannot dispute itself" in p for p in job.invalid_counters)
    assert any("the claim itself was dropped" in p for p in job.invalid_counters)


def test_update_a_new_claim_disputes_an_existing_one() -> None:
    job = ThreadClaims(_thread(), new_ids=frozenset({"D"}))
    existing = [_existing("t-A-old1", ["A"]), _existing("t-A-old2", ["B"])]
    snapshot = copy.deepcopy(existing)
    answer = {
        "claims": [_claim("Alpha changed its price", [4], counters=[], counters_existing=[2]),
                   _claim("nonsense ref", [4], counters=[5], counters_existing=[7])],
        "existing": [{"claim": 2, "supporting": [], "opposing": [4]}],
    }
    old1, old2, new, other = job.absorb(answer, existing)
    assert old2["counters"] == [new["claim_id"]] and new["counters"] == [old2["claim_id"]]
    assert old2["opposing_message_ids"] == ["D"]
    assert "counters" not in old1 and other["counters"] == []
    assert len(job.invalid_counters) == 2
    assert existing == snapshot  # the previous output is not mutated


def test_update_answers_must_carry_counters_existing() -> None:
    job = ThreadClaims(_thread(), new_ids=frozenset({"D"}))
    with pytest.raises(OutputError, match="counters_existing"):
        job.absorb({"claims": [_claim("x", [4])], "existing": []}, [_existing("t-A-old", ["A"])])
    with pytest.raises(OutputError, match="counters"):
        ThreadClaims(_thread()).absorb({"claims": [_claim("x", [1], counters=["1"])]}, [])


def test_finalize_all_drops_links_to_claims_that_left_the_output() -> None:
    job = ThreadClaims(_thread())
    a, b = job.absorb({"claims": [_claim("a", [1], [2]), _claim("b", [2], counters=[1])]}, [])
    b["supporting_message_ids"] = ["GONE"]  # b's message left the thread
    kept, dropped = job.finalize_all([a, b])
    assert dropped == 1 and [c["claim_id"] for c, _ in kept] == [a["claim_id"]]
    assert kept[0][0]["counters"] == []
    keys = list(kept[0][0])
    assert keys[keys.index("opposing_message_ids") + 1] == "counters"


def test_consistency_check_finds_each_kind_of_mismatch() -> None:
    def claim(cid: str, supporting: list[str], opposing: list[str], counters: list[str]) -> dict:
        return {"claim_id": cid, "supporting_message_ids": supporting,
                "opposing_message_ids": opposing, "counters": counters}

    claims = [
        claim("a", ["A"], ["B"], ["b"]), claim("b", ["B"], [], ["a"]),        # consistent
        claim("c", ["C"], ["D"], []),                                         # a bare count
        claim("e", ["E"], ["F", "G"], ["f"]), claim("f", ["F"], [], ["e"]),   # G unanswered
        claim("h", ["H"], [], ["i"]), claim("i", ["I"], [], ["h"]),           # no shared message
    ]
    assert check_counters(claims) == {
        "opposed_without_counter_claim": ["c"],
        "opposing_not_in_counter": ["e"],
        "link_without_shared_message": ["h i"],
    }


# ---------------------------------------------------------------- step 3 run(), fake model

@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(tmp_path)


ALPHA = claim_id_for("t-1", "Alpha is recommended")
DISSENT_TEXT = "Alpha lost the documents; call 050-1234567 instead"
DISSENT = claim_id_for("t-1", DISSENT_TEXT)
LATE_TEXT = "Alpha raised its fee in 2024"
LATE = claim_id_for("t-1", LATE_TEXT)


def _fresh_with_dissent(user: str, schema: dict) -> dict:
    """t-1: message 2 answers message 1 with a reason -> a dissent claim (with a phone)."""
    assert schema == FRESH_SCHEMA
    if "alpha is good" not in user:
        return {"claims": []}
    return {"claims": [_claim("Alpha is recommended", [1], [2]),
                       _claim(DISSENT_TEXT, [2], counters=[1], stance="negative")]}


def test_fresh_run_writes_mutual_links_and_scrubs_the_dissent(corpus: Corpus) -> None:
    summary = corpus.run(FakeLLM(_fresh_with_dissent))
    claims = corpus.claims()
    assert claims[ALPHA]["counters"] == [DISSENT] and claims[DISSENT]["counters"] == [ALPHA]
    assert not re.search(r"\d{3}-\d{7}", claims[DISSENT]["claim_text"])  # same scrub as any claim
    assert claims[DISSENT]["_redactions"]
    # Counts come from the evidence, as before: the dissent author plus a 👍 on the dissent.
    assert claims[ALPHA]["opposer_count"] == 2 and claims[DISSENT]["supporter_count"] == 2
    assert summary["counter_check"] == {"counter_links": 1, "opposed_without_counter_claim": 0,
                                        "opposing_not_in_counter": 0,
                                        "link_without_shared_message": 0}
    assert summary["invalid_counter_refs_dropped"] == 0


def test_update_links_a_new_dissent_to_an_existing_claim_and_keeps_manual_edits(
    corpus: Corpus,
) -> None:
    def fresh(user: str, schema: dict) -> dict:
        return {"claims": [_claim("Alpha is recommended", [1])]} if "alpha is good" in user \
            else {"claims": []}

    def update(user: str, schema: dict) -> dict:
        assert schema == UPDATE_SCHEMA and "C1: Alpha is recommended" in user
        new = [int(line[1:line.index("]")]) for line in user.splitlines() if "(חדשה)" in line]
        return {"claims": [_claim(LATE_TEXT, new, counters=[], counters_existing=[1],
                                  stance="negative")],
                "existing": [{"claim": 1, "supporting": [], "opposing": new}]}

    corpus.run(FakeLLM(fresh))
    shutil.copy(corpus.artifact.original, corpus.artifact.edited)
    edited = json.loads(corpus.artifact.edited.read_text("utf-8"))
    for claim in edited["claims"]:
        claim["claim_text"] = "fixed by hand"
    corpus.artifact.edited.write_text(json.dumps(edited), "utf-8")

    corpus.threads["t-1"].append(_message("W9", "2024-03-01", DAN, "alpha now costs more"))
    summary = corpus.run(FakeLLM(update))

    original, mine = corpus.claims(), corpus.claims(edited=True)
    assert original[ALPHA]["counters"] == [LATE] and original[LATE]["counters"] == [ALPHA]
    assert mine[ALPHA]["claim_text"] == "fixed by hand"  # the manual edit survives ...
    assert mine[ALPHA]["counters"] == [LATE]               # ... and gets the new link
    assert mine[ALPHA]["opposing_message_ids"] == ["W9"]
    assert not summary["changes"]["superseded"]
    assert summary["counter_check"]["counter_links"] == 1
    assert summary["counter_check"]["opposing_not_in_counter"] == 0


def test_a_rerun_without_changes_keeps_the_links(corpus: Corpus) -> None:
    corpus.run(FakeLLM(_fresh_with_dissent))
    before = corpus.claims()
    llm = FakeLLM(_fresh_with_dissent)
    corpus.run(llm)
    assert llm.prompts == [] and corpus.claims() == before


def test_invalid_references_are_counted_in_the_run(corpus: Corpus) -> None:
    def bad_refs(user: str, schema: dict) -> dict:
        return {"claims": [_claim("x", [1], counters=[3, 1])]}

    summary = corpus.run(FakeLLM(bad_refs))
    assert summary["invalid_counter_refs_dropped"] == 4  # two threads, two bad refs each


def test_the_new_schema_changes_the_cache_key(tmp_path: Path) -> None:
    """Answers cached for the schema without ``counters`` are never reused."""

    old = copy.deepcopy(FRESH_SCHEMA)
    item = old["properties"]["claims"]["items"]
    del item["properties"]["counters"]
    item["required"].remove("counters")
    llm = LLMClient(provider="anthropic", model="claude-sonnet-5-5", cache_dir=tmp_path,
                    failure_log=tmp_path / "failures.jsonl", thinking_param="low")
    key = llm._cache_key(EXTRACT_SYSTEM, "Conversation:\n[1] ...", json_mode=True,
                         response_schema=FRESH_SCHEMA)
    assert key != llm._cache_key(EXTRACT_SYSTEM, "Conversation:\n[1] ...", json_mode=True,
                                 response_schema=old)
