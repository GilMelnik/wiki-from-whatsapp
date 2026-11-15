"""The proof that moving the data to a new export changed nothing a step or a person decided.

``PipelineData`` is what the move reads and writes, in memory. ``Proof.of(data)`` takes what
must stay the same from it:

- the conversations (ids, and the set of message ids of each), in step 1's own file and in
  the edited one;
- step 1's view of its input: the messages it would see as new, changed or removed;
- every label in force (``is_knowledge_bearing``), which ones are hand labels, step 2's own
  records (probabilities included), and how many conversations need a label (``LabelRule``,
  what the conversations page lists);
- the claims: ids, texts, entities, stance, supporting / opposing / counter message ids (as
  sets), in step 3's own file and in the edited one;
- step 3's plan: the conversations it would read (new or changed) or drop, and per
  conversation the messages it would mark as new;
- the gold file: its windows, labels and conversations (id lists as sets).

``Proof.differences`` names what is not the same. The people counts are reported, not
compared: merging a member's two ids is the point of the move.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_1_threads_split.message import message_record
from step_2_classify.labels import MANUAL_FINGERPRINT, LabelRule
from step_3_extract.claims import input_fingerprint, message_fingerprints
from step_3_extract.run import knowledge_thread_ids
from utils.reconcile import diff_inputs, fingerprint
from utils.threads_io import export_index, person_key, with_export

Payload = dict[str, Any]


@dataclass
class PipelineData:
    """The files the move reads and writes (``None`` when a file does not exist)."""

    input: list[dict[str, Any]]
    threads: Payload | None = None
    threads_edited: Payload | None = None
    classified: Payload | None = None
    classified_edited: Payload | None = None
    claims: Payload | None = None
    claims_edited: Payload | None = None
    audit: list[dict[str, Any]] | None = None
    gold: Payload | None = None
    state: Payload | None = None

    @property
    def read_threads(self) -> Payload | None:
        """The threads the later steps read (the edited file when there is one)."""
        return self.threads_edited or self.threads

    @property
    def read_classified(self) -> Payload | None:
        return self.classified_edited or self.classified

    def export_index(self) -> dict[str, dict[str, Any]]:
        return export_index(self.input)


def _memberships(payload: Payload | None) -> dict[str, frozenset[str]]:
    return {t["thread_id"]: frozenset(m["id"] for m in t["messages"]) for t in (payload or {}).get("threads", [])}


def _claims_view(payload: Payload | None) -> dict[str, tuple[Any, ...]]:
    return {
        c["claim_id"]: (
            c["thread_id"], c["claim_text"], tuple(c.get("entities") or []), c.get("stance"),
            frozenset(c.get("supporting_message_ids") or []),
            frozenset(c.get("opposing_message_ids") or []),
            frozenset(c.get("counters") or []),
        )
        for c in (payload or {}).get("claims", [])
    }


def _without_manual(payload: Payload | None) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != MANUAL_FINGERPRINT} for r in (payload or {}).get("threads", [])]


def _gold_view(gold: Payload | None) -> tuple[Any, ...]:
    """The gold file's windows, labels and conversations, id lists as sets."""
    if gold is None:
        return ()
    windows = tuple(
        (w["start"], w["end"], frozenset(w["message_ids"]), frozenset(w.get("context_message_ids", ())))
        for w in gold.get("windows", [])
    )
    conversations = tuple(
        (c["thread_id"], frozenset(c["message_ids"])) for c in gold.get("conversations", [])
    )
    return windows, gold.get("messages"), gold.get("context_messages"), conversations


def step_1_view(data: PipelineData) -> dict[str, tuple[str, ...]]:
    """The input messages step 1 would see as new, changed or removed."""
    stored = (data.threads or {}).get("metadata", {}).get("inputs", {})
    records = (message_record(m) for m in data.input)
    current = {r["id"]: fingerprint(r) for r in records if r is not None}
    diff = diff_inputs(stored, current)
    return {"new": diff.new, "changed": diff.changed, "removed": diff.removed}


def step_3_view(data: PipelineData) -> dict[str, Any]:
    """What step 3's next run would read: conversations new or changed (with the messages
    it would mark as new) and removed; nothing when it never ran."""
    if data.claims is None or data.read_threads is None or data.read_classified is None:
        return {}
    threads = {t["thread_id"]: t for t in data.read_threads["threads"]}
    index = data.export_index()
    current = {
        tid: with_export(threads[tid], index)
        for tid in knowledge_thread_ids(data.read_classified)
        if tid in threads
    }
    metadata = data.claims["metadata"]
    diff = diff_inputs(metadata.get("inputs", {}), {tid: input_fingerprint(t) for tid, t in current.items()})
    stored = metadata.get("message_fingerprints", {})
    read = {}
    for tid in (*diff.new, *diff.changed):
        before = stored.get(tid, {})
        read[tid] = frozenset(i for i, fp in message_fingerprints(current[tid]).items() if before.get(i) != fp)
    return {"read": read, "removed": frozenset(diff.removed)}


def needs_label_count(data: PipelineData) -> int:
    if data.read_threads is None or data.classified is None:
        return 0
    rule = LabelRule(data.classified, data.classified_edited)
    return sum(1 for thread in data.read_threads["threads"] if rule.needs_label(thread))


def hand_labels(data: PipelineData) -> frozenset[str]:
    if data.classified is None:
        return frozenset()
    rule = LabelRule(data.classified, data.classified_edited)
    return frozenset(tid for tid in rule.in_force if rule.by_hand(tid))


def people(messages: Iterable[dict[str, Any]], audit: Sequence[dict[str, Any]] | None) -> dict[str, int]:
    """Distinct writers of the input, and distinct people of step 3's audit."""
    writers = {person_key(m.get("sender")) for m in messages} - {""}
    found = {p for r in audit or [] for p in r.get("participants") or []}
    found |= {e["person"] for r in audit or [] for side in ("supporters", "opposers") for e in r.get(side) or []}
    return {"writers": len(writers), "audit_people": len(found - {""})}


@dataclass(frozen=True)
class Proof:
    checks: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, data: PipelineData) -> Proof:
        labels = (data.read_classified or {}).get("threads", [])
        return cls({
            "conversations": _memberships(data.read_threads),
            "conversations of step 1's own file": _memberships(data.threads),
            "step 1 sees in its input": step_1_view(data),
            "labels in force": {r["thread_id"]: r.get("is_knowledge_bearing") for r in labels},
            "hand labels": hand_labels(data),
            "step 2's records": (_without_manual(data.classified), _without_manual(data.classified_edited)),
            "conversations that need a label": needs_label_count(data),
            "claims": _claims_view(data.claims),
            "claims of the edited file": _claims_view(data.claims_edited),
            "step 3's plan": step_3_view(data),
            "gold file (windows, labels, conversations)": _gold_view(data.gold),
        })

    def differences(self, other: Proof) -> list[str]:
        return [name for name, value in self.checks.items() if other.checks.get(name) != value]

    def lines(self) -> list[str]:
        """One line of numbers per check (no ids, no text)."""
        out = []
        for name, value in self.checks.items():
            if isinstance(value, dict) and name == "step 3's plan":
                size = f"{len(value.get('read', {}))} to read, {len(value.get('removed', ()))} to drop"
            elif isinstance(value, dict) and name == "step 1 sees in its input":
                size = ", ".join(f"{k} {len(v)}" for k, v in value.items())
            elif name.startswith("gold file"):
                size = f"{len(value[0])} windows, {len(value[3])} conversations" if value else "none"
            elif isinstance(value, (dict, frozenset, list, tuple)):
                size = f"{len(value)} items"
            else:
                size = str(value)
            out.append(f"{name}: {size}")
        return out
