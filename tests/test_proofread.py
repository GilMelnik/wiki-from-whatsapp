"""The Hebrew language edit's own rules: which replacements code applies, the check that
sends a body back, a failed call, the tested prompt and the stage's model (synthetic text)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.checks import AnswerRules, problems
from step_7_write_pages.proofread import (
    PROOFREAD_CONFIG,
    SCHEMA,
    SYSTEM,
    ProofreadJob,
    Proofreader,
    apply_edits,
    owed_frozen,
    user_message,
)
from tests.pages_fixtures import FakeProofreader
from utils.config import LLM_CONFIG, section
from utils.llm_client import LLMClient
from utils.reconcile import fingerprint

BODY = ("פתיח קצר על הדף.\n\n## חלק\n\n"
        "בשנת 2023 חברים המליצו על סוכנות א.[^g-00000001] "
        "ראו [[costs|עלויות]] ו[[label:usa|ארה\"ב]].[^g-00000002]\n"
        "משפט אחרון כאן.\n")
LEAD = "פתיח קצר על הדף."
IDS = ["g-00000001", "g-00000002", "g-00000003", "g-00000004"]
LOGGER = logging.getLogger("test-proofread")


def edit(old: Any, new: Any) -> dict[str, Any]:
    return {"old": old, "new": new, "kind": "grammar", "why": "reads better"}


@pytest.mark.parametrize("edit_,reason", [
    (edit("קצר", "קצר"), "no_change"),
    (edit("לא נמצא בטקסט", "x"), "not_unique"),
    (edit("על", "אל"), "not_unique"),  # twice in the body
    (edit("בשנת 2023", "בשנת 2024"), "fixed_changed"),
    (edit("בשנת 2023 חברים", "לפני שנתיים חברים"), "fixed_changed"),
    (edit("סוכנות א.[^g-00000001]", "סוכנות א."), "fixed_changed"),
    (edit("[[costs|עלויות]]", "[[agencies|עלויות]]"), "fixed_changed"),
    (edit("costs|עלויות", "agencies|עלויות"), "fixed_changed"),  # cuts the link in two
    (edit("[[label:usa|", "[[label:georgia|"), "fixed_changed"),
    (edit("קצר", 5), "malformed"),
    ({"old": "קצר", "new": "קצרה"}, "malformed"),
    ("קצר", "malformed"),
])
def test_an_unsafe_edit_is_dropped_with_its_reason(edit_: Any, reason: str) -> None:
    edited = apply_edits(BODY, [edit_])
    assert edited.body == BODY and edited.applied == [] and edited.dropped == {reason: 1}


def test_marks_link_targets_and_numbers_stay_while_words_and_shown_text_change() -> None:
    edits = [edit("[[costs|עלויות]]", "[[costs|העלויות]]"),
             edit("חברים המליצו על סוכנות", "החברים המליצו על הסוכנות")]
    edited = apply_edits(BODY, edits)
    assert edited.applied == edits and not edited.dropped
    assert "בשנת 2023 החברים המליצו על הסוכנות א.[^g-00000001]" in edited.body
    assert "ראו [[costs|העלויות]] ו[[label:usa|ארה\"ב]].[^g-00000002]" in edited.body


def test_an_edit_never_touches_text_an_earlier_edit_wrote() -> None:
    edits = [edit("משפט אחרון", "המשפט האחרון"),
             edit("האחרון כאן", "האחרון פה"),       # inside the first edit's text
             edit("כאן", "פה"),                      # right after it: allowed
             edit(" על הדף", ""),                    # a deletion
             edit("קצר.", "קצרה."),                  # spans the deleted text
             edit("קצר", "קצרה")]                    # before it: allowed
    edited = apply_edits(BODY, edits)
    assert edited.applied == [edits[0], edits[2], edits[3], edits[5]]
    assert edited.dropped == {"overlap": 2}
    assert edited.body.startswith("פתיח קצרה.\n") and edited.body.endswith("המשפט האחרון פה.\n")


def test_a_sentence_the_update_kept_is_never_edited() -> None:
    edits = [edit("פתיח קצר", "פתיחה קצרה"), edit("\n\n## חלק", "\n\n## החלק"),
             edit("משפט אחרון כאן", "המשפט האחרון כאן")]
    edited = apply_edits(BODY, edits, frozen=[LEAD, "משפט אחרון כאן."])
    assert edited.applied == [edits[1]] and edited.dropped == {"frozen": 2}
    assert edited.body.startswith(f"{LEAD}\n\n## החלק\n")


def _rules(**changes: Any) -> AnswerRules:
    return AnswerRules(page_id="p", allowed_ids=frozenset(IDS), main_ids=tuple(IDS),
                       required_ids=(), page_targets=frozenset({"p", "costs"}),
                       label_targets=frozenset({"usa"}),
                       backing={gid: (1, "2024-01-01") for gid in IDS}, **changes)


ANSWER = {"body": "פתיח.\n\nאחד.[^g-00000001][^g-00000002] שניים.[^g-00000003][^g-00000004]\n",
          "points": [{"kind": "fact", "statement": f"נקודה {gid}", "groups": [gid]} for gid in IDS],
          "left_out": []}


def test_edits_that_break_a_check_send_the_body_back() -> None:
    assert problems(ANSWER, _rules()) == []
    merge = edit("אחד.[^g-00000001][^g-00000002] שניים", "אחד[^g-00000001][^g-00000002] וגם שניים")
    fake = FakeProofreader(lambda page_id, body: [edit("פתיח.", "פתיחה."), merge])
    outcome = Proofreader(fake, LOGGER).run([ProofreadJob("p", ANSWER, _rules())])["p"]
    assert outcome.body == ANSWER["body"] and not outcome.failed
    assert outcome.language_edits["applied"] == [] and outcome.language_edits["dropped"] == {
        "reverted": 2}
    assert "more than 3 marks" in outcome.language_edits["reverted"][0]


def test_a_failed_call_keeps_the_body_and_records_what_was_kept() -> None:
    kept = ["אחד.[^g-00000001][^g-00000002]"]
    jobs = [ProofreadJob("p", ANSWER, _rules(frozen=frozenset(kept))),
            ProofreadJob("q", ANSWER, _rules())]
    fake = FakeProofreader(lambda page_id, body: [edit("שניים", "שתיים")], fail={"p"})
    outcomes = Proofreader(fake, LOGGER).run(jobs)
    assert fake.batches == [["p", "q"]]
    failed = outcomes["p"]
    assert failed.failed and failed.body == ANSWER["body"]
    assert failed.language_edits["model"] == "fake-proofread"
    assert failed.language_edits["frozen"] == [fingerprint(kept[0])]
    assert owed_frozen(failed.language_edits, failed.body) == kept
    assert owed_frozen(outcomes["q"].language_edits, outcomes["q"].body) is None
    assert "שתיים" in outcomes["q"].body


def test_without_a_batch_api_each_page_is_one_call() -> None:
    fake = FakeProofreader(fail={"q"}, batch=False)
    outcomes = Proofreader(fake, LOGGER).run([ProofreadJob(pid, ANSWER, _rules()) for pid in "pq"])
    assert fake.calls == ["p", "q"] and fake.batches == []
    assert not outcomes["p"].failed and outcomes["q"].failed


def test_a_batch_that_cannot_start_fails_every_page_alone() -> None:
    class Broken(FakeProofreader):
        def complete_batch_json(self, requests: Any) -> dict[str, Any]:
            raise RuntimeError("the batch was not created")

    outcomes = Proofreader(Broken(), LOGGER).run([ProofreadJob(pid, ANSWER, _rules()) for pid in "pq"])
    assert all(o.failed and "not created" in o.language_edits["failed"] for o in outcomes.values())


def test_the_prompt_and_schema_are_the_tested_ones() -> None:
    """Task 57's comparison of four models ran with exactly this prompt and schema."""
    assert hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest() == (
        "c4afe856bd34dde04d1f92c8c2302cae2d9978b65bcc74b088bc711c10361e57")
    assert hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode("utf-8")).hexdigest() == (
        "4b85bdd8eb3c4a7273fcff25e096b8355d61c709f923781b9811288a95fe5973")
    assert user_message("גוף") == "<article>\nגוף\n</article>"


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-2"])
def test_the_stage_sends_only_what_opus_and_fable_accept(model: str, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    stage = LLM_CONFIG["stages"][PROOFREAD_CONFIG["stage"]]
    assert (stage["provider"], stage["model"], stage["thinking_param"]) == (
        "anthropic", "claude-opus-5-5", "high")
    monkeypatch.setitem(stage, "model", model)
    llm = LLMClient.for_stage(PROOFREAD_CONFIG["stage"], cache_dir=tmp_path,
                              max_tokens=PROOFREAD_CONFIG["max_output_tokens"])
    params = llm._provider._message_params(SYSTEM, user_message("גוף"), SCHEMA)
    assert params["model"] == model and params["max_tokens"] == 64000
    assert params["output_config"] == {"effort": "high",
                                       "format": {"type": "json_schema", "schema": SCHEMA}}
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(params)
    assert model in section("update")["estimate"]["usd_per_million_tokens"]
