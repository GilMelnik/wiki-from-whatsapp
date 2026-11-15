"""Tagged members in step 3 (task 67): how a tag is shown to the model, the fingerprints, which
conversations a run reads again, the owner's table and the prompt line. Synthetic data only
(made-up member ids and provider names); fake model."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from step_3_extract.claims import input_fingerprint, message_fingerprints
from step_3_extract.prompt import EXTRACT_SYSTEM, FRESH_SCHEMA
from step_3_extract.run import run
from step_3_extract.tagged import load_table, provider_names, tagged_members, waiting_members
from utils.paths import Artifact
from utils.reconcile import fingerprint
from utils.threads_io import RENDER_CONFIG, export_index, render_thread_for_llm, with_export

ALICE, BOB, CAROL, DAN, ERIN = (f"97250000000{i}@s.whatsapp.net" for i in range(1, 6))
BOB_TAG, DAN_TAG, ERIN_TAG = "97250000000002", "11110000000004", "97250000000005"
PROVIDER = "Clinic Alpha"
CLAIM_NUMBERS = itertools.count(1)  # every answer a new claim text, so a re-read claim has a new id


def holds_a_tag_number(text: str) -> bool:
    return any(f"@{tag}" in text for tag in (BOB_TAG, DAN_TAG, ERIN_TAG))


def alias(n: int) -> str:
    return RENDER_CONFIG["alias"].format(n=n)


def entry(wa_id: str, day: str, sender: str, text: str, tags: dict[str, str] | None = None, **extra: Any) -> dict:
    """One export message; ``tags``: tag digits -> member id (the export's ``mentions``)."""
    return {"id": wa_id, "date": f"{day[8:10]}/{day[5:7]}/{day[:4]}", "time": "10:00:00",
            "sender": {"user_name": sender, "phone": None}, "text": text, "reactions": [],
            "mentions": [{"tag": tag, "member": member} for tag, member in (tags or {}).items()],
            **extra}


def thread_of(thread_id: str, entries: list[dict]) -> dict:
    return {"thread_id": thread_id, "messages": [
        {"id": e["id"], "datetime": f"{e['date'][6:]}-{e['date'][3:5]}-{e['date'][:2]}T10:00:00",
         "sender": e["sender"]["user_name"], "content": e["text"]} for e in entries]}


EXPORT = [
    entry("W1", "2024-01-05", ALICE, "ask @97250000000002 about it", {BOB_TAG: BOB}),
    entry("W2", "2024-01-05", CAROL, "@11110000000004 is great", {DAN_TAG: DAN}),
    entry("W3", "2024-01-06", BOB, "thanks, call @0501234567"),
    entry("W4", "2024-01-07", CAROL, "agreed",
          quote={"sender": {"user_name": ALICE}, "text": "and @97250000000005 too"}),
    entry("W5", "2024-01-08", ERIN, "hello @97250000000005", {ERIN_TAG: ERIN}),
]


def test_a_tag_is_shown_as_the_provider_name_or_the_members_alias_never_as_a_number() -> None:
    thread = with_export(thread_of("t-W1", EXPORT[:4]), export_index(EXPORT))
    text, refs = render_thread_for_llm(thread, providers={DAN: PROVIDER})
    assert refs == ["W1", "W2", "W3", "W4"]
    assert text.splitlines() == [
        f"[1] 2024-01-05 {alias(1)}: ask @{alias(2)} about it",  # Bob, tagged before he speaks
        f"[2] 2024-01-05 {alias(3)}: @{PROVIDER} is great",
        f"[3] 2024-01-06 {alias(2)}: thanks, call @0501234567",  # keeps his alias; not a tag
        # A quoted text's tag is read through every tag the export lists (Erin's own message).
        f"[4] 2024-01-07 {alias(3)} {RENDER_CONFIG['reply_mark']}«{alias(1)}: and @{alias(4)} too»: agreed",
    ]
    assert not holds_a_tag_number(text)


def test_without_the_export_nothing_is_joined_and_the_text_stays_as_it_is() -> None:
    text, _ = render_thread_for_llm(with_export(thread_of("t-W1", EXPORT[:1]), None))
    assert text.endswith("ask @97250000000002 about it")


def test_only_a_tagging_message_has_its_fingerprint_follow_the_table() -> None:
    thread = with_export(thread_of("t-W1", EXPORT[:4]), export_index(EXPORT))
    undecided = message_fingerprints(thread)
    named = message_fingerprints(thread, {DAN: PROVIDER})
    renamed = message_fingerprints(thread, {DAN: "Clinic Beta"})
    assert undecided["W2"] != named["W2"] != renamed["W2"]
    for wa_id in ("W1", "W3", "W4"):  # tags of members who are not providers show the same
        assert undecided[wa_id] == named[wa_id] == renamed[wa_id]
    # A message without a tag keeps the fingerprint stored before tags were shown at all.
    w3 = thread["messages"][2]
    assert named["W3"] == fingerprint([w3["datetime"], w3["sender"], w3["content"], None])[:12]
    untagged = with_export(thread_of("t-W3", EXPORT[2:3]), export_index(EXPORT))
    assert input_fingerprint(untagged, {DAN: PROVIDER}) == input_fingerprint(untagged)


class FakeLLM:
    provider = "fake"
    model = "fake-extract"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.schemas: list[dict] = []

    def supports_batch(self) -> bool:
        return False

    def set_logger(self, logger: Any) -> None:
        del logger

    def complete_json(self, system: str, user: str, task: str = "", response_schema: dict | None = None) -> Any:
        del system, task
        self.prompts.append(user)
        self.schemas.append(response_schema)
        return {"claims": [{"n": 1, "text": f"claim {next(CLAIM_NUMBERS)}", "entities": [], "stance": "factual",
                            "supporting": [1], "opposing": [], "counters": []}]}


class Corpus:
    """t-A tags Dan, t-B tags Erin, t-C has no tag; files and step-3 paths under ``root``."""

    THREADS = {"t-A": ["W1", "W2"], "t-B": ["W5"], "t-C": ["W3"]}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.table = root / "tagged_members.json"
        self.artifact = Artifact(root / "claims.json", root / "claims_edited.json")
        by_id = {e["id"]: e for e in EXPORT}
        threads = [thread_of(tid, [by_id[i] for i in ids]) for tid, ids in self.THREADS.items()]
        (root / "threads.json").write_text(json.dumps({"threads": threads}), "utf-8")
        classified = [{"thread_id": tid, "is_knowledge_bearing": True} for tid in self.THREADS]
        (root / "classified.json").write_text(json.dumps({"threads": classified}), "utf-8")
        self.export(with_tags=True)

    def export(self, *, with_tags: bool) -> None:
        entries = [{**e, "mentions": e["mentions"] if with_tags else []} for e in EXPORT]
        (self.root / "export.json").write_text(json.dumps(entries), "utf-8")

    def decide(self, members: dict[str, dict]) -> None:
        self.table.write_text(json.dumps({"members": members}), "utf-8")

    def run(self, **kwargs: Any) -> tuple[FakeLLM, dict]:
        llm = FakeLLM()
        result = run(threads_path=self.root / "threads.json", classified_path=self.root / "classified.json",
                     export_path=self.root / "export.json", tagged_path=self.table, artifact=self.artifact,
                     audit_path=self.root / "audit.json", llm=llm, use_batch=False, **kwargs)
        return llm, result

    def claim_ids(self) -> dict[str, set[str]]:
        claims = json.loads(self.artifact.original.read_text("utf-8"))["claims"]
        return {tid: {c["claim_id"] for c in claims if c["thread_id"] == tid} for tid in self.THREADS}


def test_conversations_whose_tags_show_differently_are_read_afresh_and_no_other(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    corpus.export(with_tags=False)  # claims written before tags were shown
    corpus.run()
    before = corpus.claim_ids()

    corpus.export(with_tags=True)  # the first run that shows tags: every conversation with one
    llm, result = corpus.run()
    assert len(llm.prompts) == 2 and result["retagged_threads"] == 2
    assert all(schema == FRESH_SCHEMA and "Existing claims" not in prompt
               for prompt, schema in zip(llm.prompts, llm.schemas))
    assert not any(holds_a_tag_number(prompt) for prompt in llm.prompts)
    after = corpus.claim_ids()
    assert after["t-C"] == before["t-C"]
    assert all(after[tid] and not after[tid] & before[tid] for tid in ("t-A", "t-B"))  # replaced

    assert corpus.run()[0].prompts == []  # nothing changed

    corpus.decide({DAN: {"provider": True, "public_name": PROVIDER, "decided_at": "2026-11-15T00:00:00Z"}})
    llm, result = corpus.run()
    assert len(llm.prompts) == 1 and f"@{PROVIDER} is great" in llm.prompts[0]
    assert result["retagged_threads"] == 1 and llm.schemas == [FRESH_SCHEMA]
    final = corpus.claim_ids()
    assert final["t-B"] == after["t-B"] and final["t-C"] == after["t-C"]
    assert final["t-A"] and not final["t-A"] & after["t-A"]

    # "Not a provider" shows the tag as an alias, as undecided did: nothing to read again.
    corpus.decide({DAN: {"provider": True, "public_name": PROVIDER, "decided_at": "2026-11-15T00:00:00Z"},
                   ERIN: {"provider": False, "decided_at": "2026-11-15T00:00:00Z"}})
    assert corpus.run()[0].prompts == []
    llm, result = corpus.run(estimate=True, count_tokens=lambda system, user: len(user))
    assert result["model_calls"] == 0 and llm.prompts == []


def test_the_owners_table_and_who_waits_for_a_decision(tmp_path: Path) -> None:
    table = tmp_path / "tagged_members.json"
    assert load_table(table) == {}
    table.write_text(json.dumps({"members": {
        DAN: {"provider": True, "public_name": f"  {PROVIDER} ", "decided_at": "x"},
        ERIN: {"provider": False, "decided_at": "x"},
        BOB: {"provider": True, "public_name": " ", "decided_at": "x"},
    }}), "utf-8")
    assert provider_names(load_table(table)) == {DAN: PROVIDER}
    table.write_text(json.dumps({"providers": []}), "utf-8")
    with pytest.raises(ValueError, match="not the tagged members table"):
        load_table(table)

    entries = [
        entry("A1", "2024-01-01", ALICE, "@1 @1", {"1": DAN}),  # one message tags once
        entry("A2", "2024-02-01", BOB, "@1", {"1": DAN}),
        entry("A3", "2024-03-01", CAROL, "@2", {"2": DAN}),  # another way to write Dan's tag
        entry("A4", "2024-03-02", DAN, "@2 me", {"2": DAN}),  # tagging oneself: not a tagger
        entry("A5", "2024-03-03", ALICE, "@3", {"3": ERIN}),
    ]
    found = tagged_members(entries)
    assert set(found) == {DAN, ERIN}
    assert (found[DAN].tags, found[DAN].message_ids, found[DAN].taggers) == (
        ["1", "2"], ["A1", "A2", "A3", "A4"], {ALICE, BOB, CAROL})
    assert (found[DAN].first, found[DAN].last) == ("2024-01-01T10:00:00", "2024-03-02T10:00:00")
    export = tmp_path / "export.json"
    export.write_text(json.dumps(entries), "utf-8")
    assert waiting_members(export, tmp_path / "none.json") == 1  # Dan: 3 different taggers
    assert waiting_members(export, tmp_path / "none.json", min_taggers=1) == 2
    table.write_text(json.dumps({"members": {DAN: {"provider": False, "decided_at": "x"}}}), "utf-8")
    assert waiting_members(export, table) == 0
    assert waiting_members(tmp_path / "missing.json", table) == 0


def test_the_prompt_carries_the_approved_line_in_the_privacy_bullet() -> None:
    privacy = next(line for line in EXTRACT_SYSTEM.splitlines() if line.startswith("- Privacy:"))
    assert privacy.endswith(
        'A name right after "@" is a group member the speaker tagged, confirmed as a business or a '
        "professional: treat that name like any named provider (what is said about them is a claim "
        f'with that name as its entity). "@{alias("N")}" is a tagged private member: never an entity, '
        "never named."
    )
