"""Step 3 ``run()``: C3 output, private audit, and incremental re-runs (C14), fake LLM.

Synthetic data only.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from step_3_extract.claims import claim_id_for
from step_3_extract.prompt import FRESH_SCHEMA, UPDATE_SCHEMA
from step_3_extract.run import run
from utils.paths import Artifact

ALICE, BOB, CAROL, DAN = (f"97250000000{i}@s.whatsapp.net" for i in range(1, 5))
Answer = Callable[[str, dict], Any]


class FakeLLM:
    """Records every request; ``answer(prompt, schema)`` decides the reply (raise to fail)."""

    provider = "fake"
    model = "fake-extract"

    def __init__(self, answer: Answer, *, batch: bool = False) -> None:
        self.answer = answer
        self.batch = batch
        self.prompts: list[str] = []
        self.batch_calls = 0

    def supports_batch(self) -> bool:
        return self.batch

    def set_logger(self, logger: Any) -> None:
        del logger

    def complete_json(self, system: str, user: str, task: str = "", response_schema: dict | None = None) -> Any:
        del system, task
        self.prompts.append(user)
        return self.answer(user, response_schema)

    def complete_batch_json(self, requests: list[Any]) -> dict[str, Any]:
        self.batch_calls += 1
        out = {}
        for req in requests:
            self.prompts.append(req.user)
            try:
                out[req.request_id] = self.answer(req.user, req.response_schema)
            except RuntimeError:
                continue
        return out


def _claim(text: str, supporting: list[int], opposing: list[int] = (), **links: list[int]) -> dict:
    """One claim of a model answer; ``links`` = ``counters`` / ``counters_existing``."""
    return {"text": text, "entities": ["Alpha"], "stance": "positive",
            "supporting": supporting, "opposing": list(opposing), "counters": [], **links}


def answer_by_text(user: str, schema: dict) -> dict:
    """Fresh: claim per thread content. Update: back existing C1 with every new message."""

    shown = (re.match(r"^\[(\d+)\][^:]*: (.*?)(?: \[תגובות: .*)?$", line) for line in user.splitlines())
    numbers = {m.group(2): int(m.group(1)) for m in shown if m}
    if schema is FRESH_SCHEMA or schema == FRESH_SCHEMA:
        claims = []
        if "alpha is good" in numbers:
            claims.append(_claim("Alpha is recommended", [numbers["alpha is good"]],
                                 [numbers["alpha was bad"]] if "alpha was bad" in numbers else []))
        if "beta costs 5" in numbers:
            claims.append(_claim("Beta costs 5", [numbers["beta costs 5"]]))
        return {"claims": claims}
    assert schema == UPDATE_SCHEMA
    new = [int(line[1:line.index("]")]) for line in user.splitlines() if "(חדשה)" in line]
    return {"claims": [_claim("Alpha answers fast", new, counters_existing=[])],
            "existing": [{"claim": 1, "supporting": new, "opposing": []}]}


def _message(wa_id: str, day: str, sender: str, content: str) -> dict:
    return {"id": wa_id, "datetime": f"{day}T10:00:00", "sender": sender, "content": content}


class Corpus:
    """Threads / classified / export files plus the step-3 artifact paths, in ``tmp_path``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.threads = {
            "t-1": [_message("W1", "2024-01-05", ALICE, "alpha is good"),
                    _message("W2", "2024-01-06", BOB, "alpha was bad")],
            "t-2": [_message("W5", "2024-02-01", CAROL, "beta costs 5")],
        }
        self.knowledge = {"t-1": True, "t-2": True}
        self.export: dict[str, dict] = {
            "W1": {"reactions": [{"emoji": "👍", "senders": [{"user_name": CAROL}]}]},
            "W2": {"reactions": [{"emoji": "👍", "senders": [{"user_name": DAN}]}],
                   "quote": {"sender": {"user_name": ALICE}, "text": "alpha is good"}},
        }
        self.artifact = Artifact(root / "claims.json", root / "claims_edited.json")
        self.audit = root / "audit" / "claims_audit.json"

    def write(self) -> None:
        threads = [{"thread_id": tid, "messages": msgs} for tid, msgs in self.threads.items()]
        (self.root / "threads.json").write_text(json.dumps({"threads": threads}), "utf-8")
        records = [{"thread_id": tid, "passed_heuristic": True, "p_knowledge": 0.9 if kb else 0.1,
                    "is_knowledge_bearing": kb} for tid, kb in self.knowledge.items()]
        (self.root / "classified.json").write_text(json.dumps({"threads": records}), "utf-8")
        export = [{"id": i, "quote": e.get("quote"), "reactions": e.get("reactions", [])}
                  for i, e in self.export.items()]
        (self.root / "export.json").write_text(json.dumps(export), "utf-8")

    def run(self, llm: FakeLLM, **kwargs: Any) -> dict:
        self.write()
        return run(threads_path=self.root / "threads.json",
                   classified_path=self.root / "classified.json",
                   export_path=self.root / "export.json", artifact=self.artifact,
                   audit_path=self.audit, llm=llm, use_batch=llm.batch, **kwargs)

    def claims(self, edited: bool = False) -> dict[str, dict]:
        path = self.artifact.edited if edited else self.artifact.original
        return {c["claim_id"]: c for c in json.loads(path.read_text("utf-8"))["claims"]}

    def audit_records(self) -> dict[str, dict]:
        return {r["claim_id"]: r for r in json.loads(self.audit.read_text("utf-8"))["audit"]}


ALPHA = claim_id_for("t-1", "Alpha is recommended")
BETA = claim_id_for("t-2", "Beta costs 5")
FAST = claim_id_for("t-1", "Alpha answers fast")


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(tmp_path)


def test_fresh_run_writes_c3_claims_and_private_audit(corpus: Corpus) -> None:
    llm = FakeLLM(answer_by_text)
    summary = corpus.run(llm)
    assert summary["model_calls"] == 2 and summary["new_claims"] == 2
    claims = corpus.claims()
    assert set(claims) == {ALPHA, BETA}
    alpha = claims[ALPHA]
    assert alpha["supporting_message_ids"] == ["W1"] and alpha["opposing_message_ids"] == ["W2"]
    assert (alpha["first_date"], alpha["last_date"]) == ("2024-01-05", "2024-01-06")
    # Supporters: Alice (wrote W1), Carol (👍 W1). Opposers: Bob (wrote W2), Dan (👍 on W2).
    assert (alpha["supporter_count"], alpha["opposer_count"]) == (2, 2)
    # Participants: the people who wrote in the thread (Carol and Dan only reacted in t-1).
    assert (alpha["participant_count"], claims[BETA]["participant_count"]) == (2, 1)
    public = corpus.artifact.original.read_text("utf-8")
    assert "whatsapp.net" not in public and "9725" not in public
    audit = corpus.audit_records()[ALPHA]
    assert {(e["person"], e["via"]) for e in audit["opposers"]} == {(BOB, "message"), (DAN, "reaction")}
    assert all(set(e) == {"person", "date", "via", "message_id"} for e in audit["supporters"])
    # The quote of W1 is shown as a reply to message 1; no identities reach the model.
    assert any("↩[1]" in p for p in llm.prompts)
    assert not any("whatsapp.net" in p for p in llm.prompts)


def test_unchanged_rerun_makes_no_calls_and_keeps_the_files(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    before = corpus.artifact.original.read_text("utf-8")
    llm = FakeLLM(answer_by_text)
    summary = corpus.run(llm)
    assert llm.prompts == [] and summary["model_calls"] == 0
    assert json.loads(corpus.artifact.original.read_text("utf-8"))["claims"] == json.loads(before)["claims"]


def test_claims_written_before_the_count_gain_it_without_a_call(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    payload = json.loads(corpus.artifact.original.read_text("utf-8"))
    for claim in payload["claims"]:
        del claim["participant_count"]
    corpus.artifact.original.write_text(json.dumps(payload), "utf-8")
    llm = FakeLLM(answer_by_text)
    corpus.run(llm)
    assert llm.prompts == []
    assert {cid: c["participant_count"] for cid, c in corpus.claims().items()} == {ALPHA: 2, BETA: 1}
    assert corpus.audit_records()[ALPHA]["participants"] == sorted([ALICE, BOB])


def test_reaction_only_change_recomputes_counts_without_a_call(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    corpus.export["W1"]["reactions"][0]["senders"].append({"user_name": DAN})
    llm = FakeLLM(answer_by_text)
    corpus.run(llm)
    assert llm.prompts == []
    alpha = corpus.claims()[ALPHA]
    assert alpha["supporter_count"] == 3 and alpha["claim_text"] == "Alpha is recommended"


def test_changed_thread_keeps_existing_claims_and_appends_new_ones(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    first = corpus.claims()
    corpus.threads["t-1"].append(_message("W9", "2024-03-01", DAN, "alpha again"))
    llm = FakeLLM(answer_by_text)
    summary = corpus.run(llm)
    assert len(llm.prompts) == 1  # only t-1
    prompt = llm.prompts[0]
    assert "C1: Alpha is recommended" in prompt
    assert [line for line in prompt.splitlines() if "(חדשה)" in line] == [
        line for line in prompt.splitlines() if line.startswith("[3]")
    ]
    claims = corpus.claims()
    assert list(claims) == [ALPHA, FAST, BETA]
    alpha = claims[ALPHA]
    assert alpha["claim_text"] == first[ALPHA]["claim_text"]
    assert alpha["supporting_message_ids"] == ["W1", "W9"]
    assert alpha["last_date"] == "2024-03-01" and alpha["supporter_count"] == 3
    assert alpha["participant_count"] == 3 and claims[FAST]["participant_count"] == 3  # Dan wrote
    assert claims[FAST]["supporting_message_ids"] == ["W9"]
    assert claims[BETA] == first[BETA]
    assert summary["new_claims"] == 1 and set(corpus.audit_records()) == set(claims)


def test_manual_edits_survive_evidence_changes(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    shutil.copy(corpus.artifact.original, corpus.artifact.edited)
    edited = json.loads(corpus.artifact.edited.read_text("utf-8"))
    edited["claims"] = [c for c in edited["claims"] if c["claim_id"] != BETA]  # deleted
    for claim in edited["claims"]:
        claim["claim_text"] = "scrubbed by hand"
        claim["_pii_review"] = "accepted"
    corpus.artifact.edited.write_text(json.dumps(edited), "utf-8")

    corpus.threads["t-1"].append(_message("W9", "2024-03-01", DAN, "alpha again"))
    corpus.export["W5"] = {"reactions": [{"emoji": "👍", "senders": [{"user_name": ALICE}]}]}
    corpus.run(FakeLLM(answer_by_text))

    mine = corpus.claims(edited=True)
    assert BETA not in mine  # a deletion is not undone by a count change
    assert mine[ALPHA]["claim_text"] == "scrubbed by hand"
    assert mine[ALPHA]["_pii_review"] == "accepted"
    assert mine[ALPHA]["supporting_message_ids"] == ["W1", "W9"]
    assert mine[ALPHA]["last_date"] == "2024-03-01"
    assert mine[ALPHA]["participant_count"] == 3  # a derived field: refreshed on the edit
    assert FAST in mine
    assert corpus.claims()[BETA]["supporter_count"] == 2  # the original keeps the step's view


def test_new_and_removed_threads(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    corpus.threads["t-3"] = [_message("W7", "2024-04-01", BOB, "beta costs 5")]
    corpus.knowledge["t-3"] = True
    corpus.knowledge["t-2"] = False
    llm = FakeLLM(answer_by_text)
    summary = corpus.run(llm)
    assert len(llm.prompts) == 1 and summary["removed_threads"] == 1
    assert set(corpus.claims()) == {ALPHA, claim_id_for("t-3", "Beta costs 5")}
    meta = json.loads(corpus.artifact.original.read_text("utf-8"))["metadata"]
    assert set(meta["inputs"]) == {"t-1", "t-3"}


def test_failed_thread_is_retried_on_the_next_run(corpus: Corpus) -> None:
    def failing(user: str, schema: dict) -> dict:
        if "beta costs 5" in user:
            raise RuntimeError("model unavailable")
        return answer_by_text(user, schema)

    summary = corpus.run(FakeLLM(failing))
    assert summary["failed_threads"] == ["t-2"]
    meta = json.loads(corpus.artifact.original.read_text("utf-8"))["metadata"]
    assert "t-2" not in meta["inputs"]
    llm = FakeLLM(answer_by_text)
    corpus.run(llm)
    assert len(llm.prompts) == 1 and BETA in corpus.claims()


def test_malformed_answer_counts_as_a_failure(corpus: Corpus) -> None:
    summary = corpus.run(FakeLLM(lambda user, schema: {"claims": [{"text": 1}]}))
    assert sorted(summary["failed_threads"]) == ["t-1", "t-2"]
    assert corpus.claims() == {}


def test_only_restricts_the_work(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    before = corpus.claims()
    corpus.threads["t-1"].append(_message("W9", "2024-03-01", DAN, "alpha again"))
    corpus.threads["t-2"].append(_message("W6", "2024-03-02", DAN, "beta again"))
    llm = FakeLLM(answer_by_text)
    corpus.run(llm, only=["t-2"])
    assert len(llm.prompts) == 1 and "beta costs 5" in llm.prompts[0]
    assert corpus.claims()[ALPHA] == before[ALPHA]
    meta = json.loads(corpus.artifact.original.read_text("utf-8"))["metadata"]
    llm = FakeLLM(answer_by_text)
    corpus.run(llm)  # t-1 kept its old fingerprint, so it is still pending
    assert len(llm.prompts) == 1 and "alpha is good" in llm.prompts[0]
    assert set(corpus.audit_records()) == set(corpus.claims())
    assert meta["inputs"]["t-1"] != json.loads(corpus.artifact.original.read_text("utf-8"))["metadata"]["inputs"]["t-1"]


def test_rebuild_extracts_everything_again(corpus: Corpus) -> None:
    corpus.run(FakeLLM(answer_by_text))
    llm = FakeLLM(answer_by_text)
    summary = corpus.run(llm, rebuild=True)
    assert len(llm.prompts) == 2 and summary["changes"]["added"]


def test_batch_mode_sends_one_batch(corpus: Corpus) -> None:
    llm = FakeLLM(answer_by_text, batch=True)
    summary = corpus.run(llm)
    assert llm.batch_calls == 1 and summary["claims_count"] == 2


def test_phone_numbers_in_claim_text_are_scrubbed(corpus: Corpus) -> None:
    def with_phone(user: str, schema: dict) -> dict:
        return {"claims": [_claim("Call Alpha at 050-1234567", [1])]}

    corpus.run(FakeLLM(with_phone))
    claim = corpus.claims()[claim_id_for("t-1", "Call Alpha at 050-1234567")]
    assert not re.search(r"\d{3}-\d{7}", claim["claim_text"])
    assert claim["_redactions"][0]["type"] == "phone"
