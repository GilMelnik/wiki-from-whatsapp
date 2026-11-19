"""Moving the pipeline's data to an export with seconds, the database's order and member ids.

Synthetic chat: member ``P`` wrote as ``p-phone`` before and as ``p-lid`` after WhatsApp's id
change; ``Q`` is another member. The old export has minutes and the old tool's order inside a
minute; the new one has seconds, the database's order and member ids.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from review_tool.tools.threads.operations import ThreadEditor
from step_1_threads_split.message import message_record
from step_1_threads_split.splitter import SplitParams, ThreadSplitter, thread_record
from step_2_classify.labels import LabelRule, content_fingerprint, input_fingerprint
from step_3_extract.claims import ThreadClaims, message_fingerprints
from step_3_extract.claims import input_fingerprint as claims_fingerprint
from update_run.exports import NotAContinuation, check_continuation, compare_exports
from update_run.move import MovePaths, Mover, moved_data
from update_run.move_data import NotTheSameMessages
from update_run.move_proof import PipelineData, Proof, step_3_view
from utils.export_dates import parse_android_datetime
from utils.paths import BACKUPS_DIR, Artifact
from utils.reconcile import fingerprint
from utils.threads_io import export_index, with_export

MEMBERS = {"p-phone": "p-phone", "p-lid": "p-phone", "q-phone": "q-phone"}
SETTINGS = {"classifier": "test", "question": "q", "threshold": 0.5, "heuristic": "h"}


def entry(wa_id: str, time: str, sender: str, text: str, reactors: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "id": wa_id, "date": "01/03/2024", "time": time,
        "sender": {"user_name": sender, "phone": None}, "text": text, "is_outgoing": False,
        "quote": None,
        "reactions": [{"emoji": "👍", "count": len(reactors),
                       "senders": [{"user_name": r, "phone": None} for r in reactors]}] if reactors else [],
        "media_type": None, "is_forwarded": False, "is_deleted": False, "link_preview": None,
    }


# Old export: minutes, raw ids, ids sorted inside the minute (the old tool's order).
OLD = [
    entry("A1", "10:00", "p-phone", "first in the old order"),
    entry("B1", "10:00", "p-lid", "written first, shown second", reactors=("q-phone",)),
    entry("C1", "10:01", "q-phone", "an answer", reactors=("p-lid",)),
    entry("D1", "12:00", "q-phone", "another conversation"),
    entry("E1", "12:00", "q-phone", "same second, written first"),
]
# New export: seconds, member ids, the database's order (B1 before A1; E1 before D1, same second).
NEW = [
    {**entry("B1", "10:00:05", "p-phone", "written first, shown second", reactors=("q-phone",)), "mentions": []},
    {**entry("A1", "10:00:40", "p-phone", "first in the old order"), "mentions": []},
    {**entry("C1", "10:01:10", "q-phone", "an answer", reactors=("p-phone",)), "mentions": []},
    {**entry("E1", "12:00:07", "q-phone", "same second, written first"), "mentions": []},
    {**entry("D1", "12:00:07", "q-phone", "another conversation"), "mentions": []},
    {**entry("F1", "13:00:00", "q-phone", "newer than the input"), "mentions": []},
]


def _threads(groups: list[list[str]], source: list[dict[str, Any]]) -> dict[str, Any]:
    records = {r["id"]: r for r in map(message_record, source)}
    threads = [thread_record(f"t-{ids[0]}", [records[i] for i in ids]) for ids in groups]
    return {"threads": threads,
            "metadata": {"format": "threads-c1", "inputs": {i: fingerprint(r) for i, r in records.items()}}}


def _old_data(
    stale_label: bool = False,
    source: list[dict[str, Any]] = OLD,
    providers: dict[str, str] | None = None,
) -> PipelineData:
    threads = _threads([["A1", "B1", "C1"], ["D1", "E1"]], source)
    by_id = {t["thread_id"]: t for t in threads["threads"]}
    first, second = by_id["t-A1"], by_id["t-D1"]
    classified = {
        "threads": [{"thread_id": tid, "passed_heuristic": True, "p_knowledge": 0.9,
                     "is_knowledge_bearing": True} for tid in by_id],
        "metadata": {"inputs": {tid: input_fingerprint(t, SETTINGS) for tid, t in by_id.items()}, **SETTINGS},
    }
    edited = json.loads(json.dumps(classified))
    edited["threads"][0]["manual_fingerprint"] = content_fingerprint(first)
    edited["threads"][1]["is_knowledge_bearing"] = False
    edited["threads"][1]["manual_fingerprint"] = "stale" if stale_label else content_fingerprint(second)
    index = export_index(source)
    current = with_export(first, index)
    claim = {"claim_id": "t-A1-0001", "thread_id": "t-A1", "claim_text": "claim", "entities": ["X"],
             "stance": "positive", "supporting_message_ids": ["B1", "A1"], "opposing_message_ids": [],
             "counters": []}
    pairs, _ = ThreadClaims(current).finalize_all([claim])
    claims = {"claims": [pairs[0][0]],
              "metadata": {"inputs": {"t-A1": claims_fingerprint(current, providers)},
                           "message_fingerprints": {"t-A1": message_fingerprints(current, providers)}}}
    gold = {"windows": [], "messages": {},
            "conversations": [{"thread_id": "t-A1", "start": "2024-03-01T10:00:00",
                               "message_ids": ["A1", "B1", "C1"], "added_at": "x"}]}
    return PipelineData(input=source, threads=threads, threads_edited=json.loads(json.dumps(threads)),
                        classified=classified, classified_edited=edited, claims=claims,
                        audit=[pairs[0][1]], gold=gold, providers=providers or {})


def test_export_times_with_and_without_seconds() -> None:
    assert parse_android_datetime("01/03/2024", "10:00").isoformat() == "2024-03-01T10:00:00"
    assert parse_android_datetime("01/03/2024", "10:00:07").isoformat() == "2024-03-01T10:00:07"
    with pytest.raises(ValueError):
        parse_android_datetime("01/03/2024", "10")


def test_the_move_keeps_conversations_labels_and_claims() -> None:
    before = _old_data()
    after, numbers = moved_data(before, NEW, MEMBERS)
    assert Proof.of(before).differences(Proof.of(after)) == []
    first = next(t for t in after.threads_edited["threads"] if t["thread_id"] == "t-A1")
    assert [m["id"] for m in first["messages"]] == ["B1", "A1", "C1"]
    assert first["participants"] == ["p-phone", "q-phone"]
    assert first["start_time"] == "2024-03-01T10:00:05"
    assert [m["id"] for m in after.input] == ["B1", "A1", "C1", "E1", "D1"]
    assert numbers["messages whose sender id changed"] == 1
    assert numbers["people before"]["writers"] == 3 and numbers["people after"]["writers"] == 2


def test_a_hand_label_stays_valid_and_a_stale_one_stays_stale() -> None:
    for stale in (False, True):
        before = _old_data(stale_label=stale)
        after, _ = moved_data(before, NEW, MEMBERS)
        threads = after.threads_edited["threads"]
        rule = LabelRule(after.classified, after.classified_edited)
        assert [rule.needs_label(t) for t in threads] == [False, stale]
        stamps = {r["thread_id"]: r["manual_fingerprint"] for r in after.classified_edited["threads"]}
        assert stamps["t-A1"] == content_fingerprint(threads[0])
        assert (stamps["t-D1"] == "stale") is stale


def test_a_stale_step_3_fingerprint_stays_stale() -> None:
    before = _old_data()
    before.claims["metadata"]["inputs"]["t-A1"] = "stale"
    after, _ = moved_data(before, NEW, MEMBERS)
    assert after.claims["metadata"]["inputs"]["t-A1"] == "stale"
    assert after.claims["claims"] == before.claims["claims"]  # not refreshed: it is read again
    assert {e["person"] for e in after.audit[0]["supporters"]} == {"p-phone", "q-phone"}


def test_fingerprints_made_with_the_owners_table_of_providers_stay_valid() -> None:
    def tagging(export: list[dict[str, Any]]) -> list[dict[str, Any]]:  # A1 tags member Q
        tag = [{"tag": "111", "member": "q-phone"}]
        return [{**m, "text": "ask @111", "mentions": tag} if m["id"] == "A1" else m for m in export]

    before = _old_data(source=tagging(OLD), providers={"q-phone": "a public name"})
    after, numbers = moved_data(before, tagging(NEW), MEMBERS)
    assert numbers["fingerprints"]["step 3"] == {"re-stamped": 1, "left as they were": 0}
    assert step_3_view(after) == {"read": {}, "removed": frozenset()}
    assert Proof.of(before).differences(Proof.of(after)) == []


def test_a_member_under_two_ids_is_one_person_after_the_move() -> None:
    before = _old_data()
    assert before.claims["claims"][0]["supporter_count"] == 3  # p-phone, p-lid, q-phone
    after, numbers = moved_data(before, NEW, MEMBERS)
    claim = after.claims["claims"][0]
    assert claim["supporter_count"] == 2
    assert claim["supporting_message_ids"] == ["B1", "A1"]  # the conversation's new order
    assert sorted(after.audit[0]["participants"]) == ["p-phone", "q-phone"]
    assert numbers["claims"]["people counts changed"] == 1


def test_order_inside_a_second_follows_the_export() -> None:
    after, _ = moved_data(_old_data(), NEW, MEMBERS)
    second = next(t for t in after.threads["threads"] if t["thread_id"] == "t-D1")
    assert [m["id"] for m in second["messages"]] == ["E1", "D1"]
    assert second["last_sender"] == "q-phone"


def test_a_reaction_added_since_the_old_export_waits_for_the_next_update() -> None:
    later = [dict(m) for m in NEW]
    later[2] = {**entry("C1", "10:01:10", "q-phone", "an answer", reactors=("p-phone", "x-phone")),
                "mentions": []}
    before = _old_data()
    after, numbers = moved_data(before, later, MEMBERS)
    assert Proof.of(before).differences(Proof.of(after)) == []
    assert numbers["messages whose content differs beyond time and ids"] == 1
    moved_c1 = next(m for m in after.input if m["id"] == "C1")
    assert [s["user_name"] for s in moved_c1["reactions"][0]["senders"]] == ["p-phone"]
    assert moved_c1["time"] == "10:01:10"
    assert compare_exports(after.input, later).changed == ("C1",)


def test_refused_when_the_export_lacks_a_message_or_the_text_changed() -> None:
    with pytest.raises(NotTheSameMessages, match="1 missing"):
        moved_data(_old_data(), NEW[1:], MEMBERS)
    changed = [dict(NEW[0], text="other"), *NEW[1:]]
    with pytest.raises(NotTheSameMessages, match="1 text"):
        moved_data(_old_data(), changed, MEMBERS)


def _write(data: PipelineData, paths: MovePaths) -> None:
    for name, path in (("threads", paths.threads.original), ("threads_edited", paths.threads.edited),
                       ("classified", paths.classified.original),
                       ("classified_edited", paths.classified.edited),
                       ("claims", paths.claims.original), ("gold", paths.gold), ("input", paths.input)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(getattr(data, name)), encoding="utf-8")
    paths.audit.parent.mkdir(parents=True, exist_ok=True)
    paths.audit.write_text(json.dumps({"audit": data.audit, "metadata": {}}), encoding="utf-8")


def test_the_command_backs_up_writes_and_changes_nothing_the_second_time(tmp_path: Path) -> None:
    paths = MovePaths(
        input=tmp_path / "chat_android.json",
        threads=Artifact(tmp_path / "1" / "threads.json", tmp_path / "1" / "threads_edited.json"),
        classified=Artifact(tmp_path / "2" / "c.json", tmp_path / "2" / "c_edited.json"),
        claims=Artifact(tmp_path / "3" / "claims.json", tmp_path / "3" / "claims_edited.json"),
        audit=tmp_path / "3" / "audit" / "claims_audit.json",
        gold=tmp_path / "1" / "gold.json",
        state=tmp_path / "10" / "state.json",
        lease=tmp_path / "10" / "running.json",
    )
    before = _old_data()
    _write(before, paths)
    export, members = tmp_path / "export.json", tmp_path / "members.json"
    export.write_text(json.dumps(NEW), encoding="utf-8")
    members.write_text(json.dumps({"members": [{"id": "p-phone", "aliases": ["p-lid", "p-phone"]},
                                               {"id": "q-phone", "aliases": ["q-phone"]}]}), encoding="utf-8")
    mover = Mover(paths, server_check=lambda: None)

    assert mover.run(export, members, dry_run=True) == 0
    assert json.loads(paths.input.read_text(encoding="utf-8")) == OLD  # a dry run writes nothing

    backed_up = set(BACKUPS_DIR.glob("*.json")) if BACKUPS_DIR.is_dir() else set()
    assert mover.run(export, members, dry_run=False) == 0
    backups = {p.name.rsplit("_", 2)[0] for p in set(BACKUPS_DIR.glob("*.json")) - backed_up}
    assert backups == {"threads", "threads_edited", "c", "c_edited", "claims", "claims_audit", "gold",
                       "chat_android"}  # every written file was copied first
    moved = paths.load()
    assert [m["id"] for m in moved.input] == ["B1", "A1", "C1", "E1", "D1"]
    assert Proof.of(before).differences(Proof.of(moved)) == []
    assert moved.gold["conversations"][0]["start"] == "2024-03-01T10:00:05"
    assert moved.gold["conversations"][0]["message_ids"] == ["B1", "A1", "C1"]

    def stamps() -> dict[Path, int]:  # the lease file is the run's own, not data
        return {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*.json") if p != paths.lease}

    written = stamps()
    assert mover.run(export, members, dry_run=False) == 0
    assert stamps() == written

    assert Mover(paths, server_check=lambda: "busy").run(export, members, dry_run=False) == 2


def test_the_update_refuses_an_export_of_the_other_kind_and_says_what_to_do() -> None:
    moved, _ = moved_data(_old_data(), NEW, MEMBERS)
    with pytest.raises(NotAContinuation, match="Export again with the current export tool"):
        check_continuation(compare_exports(moved.input, OLD))
    with pytest.raises(NotAContinuation, match="update_run.move"):
        check_continuation(compare_exports(OLD, NEW))
    diff = compare_exports(moved.input, NEW)
    check_continuation(diff)
    assert diff.new == ("F1",) and diff.changed == () and diff.conflicting == ()


def _zeros(texts: list[str]) -> np.ndarray:
    return np.zeros((len(texts), 3), dtype=np.float32)


@pytest.mark.parametrize(
    ("order", "joins", "thread_order"),
    [({"x": 0, "y": 1}, "t-y", ["t-x", "t-y"]), ({"y": 0, "x": 1}, "t-x", ["t-y", "t-x"])],
)
def test_a_new_message_follows_the_latest_of_same_time_messages_in_chat_order(
    order: dict[str, int], joins: str, thread_order: list[str]
) -> None:
    def message(wa_id: str, moment: str) -> dict[str, Any]:
        return {"id": wa_id, "datetime": moment, "sender": "s", "content": f"text {wa_id}"}

    prior = [thread_record("t-x", [message("x", "2024-03-01T10:00:07")]),
             thread_record("t-y", [message("y", "2024-03-01T10:00:07")])]
    splitter = ThreadSplitter(SplitParams(quiet_minutes=25, resume_similarity=0.9, recent_messages=3), _zeros)
    threads = splitter.assign_new_messages(prior, [message("z", "2024-03-01T10:05:00")], {**order, "z": 2})
    assert next(t["thread_id"] for t in threads if "z" in t["message_ids"]) == joins
    assert [t["thread_id"] for t in threads] == thread_order  # same start: chat order, not id


def test_the_conversations_page_orders_same_start_conversations_by_chat_place() -> None:
    def message(wa_id: str, moment: str) -> dict[str, Any]:
        return {"id": wa_id, "datetime": moment, "sender": "s", "content": wa_id}

    threads = [thread_record("t-a", [message("a", "2024-03-01T10:00:07")]),
               thread_record("t-b", [message("b", "2024-03-01T10:00:07")]),
               thread_record("t-c", [message("c", "2024-03-01T10:00:09")])]
    edit = ThreadEditor(threads, {"b": 0, "a": 1, "c": 2}).move(["c"], "t-a")
    assert [t["thread_id"] for t in edit.threads] == ["t-b", "t-a"]
