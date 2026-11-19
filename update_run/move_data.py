"""What moving the pipeline's data to a new export of the same messages changes, computed in
memory (``update_run.move`` loads, checks, writes and proves).

The new export holds the same messages with exact times (seconds), the phone database's
order and one id per member (``data/shared/members.json`` maps every raw WhatsApp id to its
member id). ``ChatMove`` turns the data built on the old export into the same data on the new
one: which messages are in which conversation, the labels and the claims stay as they are;
only times, the order inside a conversation, sender ids and what is derived from them move.

Fingerprints are re-stamped, never recomputed blindly: a stored fingerprint that matched its
item before the move is replaced by the fingerprint of the same item after it; one that did
not match (the item was already waiting to be processed again) is left as it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_1_threads_split.message import message_record
from step_1_threads_split.splitter import thread_record
from step_2_classify.labels import (
    MANUAL_FINGERPRINT,
    SETTINGS_KEYS,
    content_fingerprint,
    input_fingerprint,
)
from step_3_extract.claims import DERIVED_KEYS, ThreadClaims, message_fingerprints
from step_3_extract.claims import input_fingerprint as claims_fingerprint
from utils.reconcile import fingerprint
from utils.threads_io import person_key, with_export

Message = dict[str, Any]
Thread = dict[str, Any]
Payload = dict[str, Any]
ExportIndex = dict[str, dict[str, Any]]


class NotTheSameMessages(ValueError):
    """The new export does not hold the input's messages as they are; nothing was changed."""


@dataclass
class Restamps:
    """How many stored fingerprints were re-stamped and how many were left as they were."""

    restamped: int = 0
    left: int = 0

    def add(self, matched: bool) -> None:
        if matched:
            self.restamped += 1
        else:
            self.left += 1


@dataclass
class ClaimsMove:
    payload: Payload
    audit: list[dict[str, Any]]
    refreshed_threads: int = 0
    counts_changed: list[str] = field(default_factory=list)
    order_changed: int = 0


def _sorted_reactions(record: Message) -> Message:
    reactions = [
        {**r, "senders": sorted(r.get("senders") or [])} for r in record.get("reactions") or []
    ]
    return {**record, "reactions": reactions} if "reactions" in record else record


class ChatMove:
    """The old pipeline input, the new export and the members table, and every move.

    ``providers`` (member id -> public name): the owner's table of tagged providers, which
    step 3's fingerprints of a tagging message hold. Member ids are the same before and
    after the move, so one table serves both sides."""

    def __init__(
        self,
        old_input: Sequence[Message],
        new_export: Sequence[Message],
        members: Mapping[str, str],
        providers: Mapping[str, str] | None = None,
    ) -> None:
        self.members = members
        self.providers = providers or {}
        by_new = {str(m["id"]): m for m in new_export}
        old_by_id = {str(m["id"]): m for m in old_input}
        self._check_same_messages(old_by_id, by_new)
        self.new_input = [m for m in new_export if str(m["id"]) in old_by_id]
        self.place = {str(m["id"]): index for index, m in enumerate(self.new_input)}
        self.old_records = {i: message_record(m) for i, m in old_by_id.items()}
        self.new_records = {str(m["id"]): message_record(m) for m in self.new_input}
        # A message whose reactions or quote differ beyond ids (a reaction added since the old
        # export) keeps the old ones in the input: the move moves, the next update brings them.
        self.content_differs = {
            i
            for i, old in self.old_records.items()
            if old is not None
            and _sorted_reactions(self.moved(old)) != _sorted_reactions(self.new_records[i])
        }
        if self.content_differs:
            self.new_input = [
                self._with_old_content(m, old_by_id[str(m["id"])])
                if str(m["id"]) in self.content_differs else m
                for m in self.new_input
            ]
            self.new_records = {str(m["id"]): message_record(m) for m in self.new_input}

    def _with_old_content(self, entry: Message, old: Message) -> Message:
        """The new export's entry with the old entry's quote and reactions, in member ids."""

        def contact(person: Any) -> dict[str, Any]:
            if isinstance(person, dict):
                return {**person, "user_name": self.member(person.get("user_name") or "")}
            return {"user_name": self.member(person or ""), "phone": None}

        quote = old.get("quote")
        return {
            **entry,
            "quote": {**quote, "sender": contact(quote.get("sender"))} if quote else quote,
            "reactions": [{**r, "senders": [contact(s) for s in r.get("senders") or []]}
                          for r in old.get("reactions") or []],
        }

    # ------------------------------------------------------------------ the messages

    def _check_same_messages(
        self, old_by_id: Mapping[str, Message], by_new: Mapping[str, Message]
    ) -> None:
        """Refuse unless every input message is in the new export: same text, same date and
        minute, and the same sender once raw ids are member ids."""
        problems: dict[str, list[str]] = {"missing": [], "text": [], "minute": [], "sender": []}
        for wa_id, old in old_by_id.items():
            new = by_new.get(wa_id)
            if new is None:
                problems["missing"].append(wa_id)
            elif (old.get("text") or "") != (new.get("text") or ""):
                problems["text"].append(wa_id)
            elif old["date"] != new["date"] or old["time"][:5] != new["time"][:5]:
                problems["minute"].append(wa_id)
            elif self.member(person_key(old.get("sender"))) != person_key(new.get("sender")):
                problems["sender"].append(wa_id)
        if any(problems.values()):
            detail = "; ".join(
                f"{len(ids)} {what} (e.g. {', '.join(ids[:3])})" for what, ids in problems.items() if ids
            )
            raise NotTheSameMessages(
                f"the new export does not hold the {len(old_by_id)} input messages as they are: "
                f"{detail} [missing = not in the export, text = other text, minute = other date "
                "or minute, sender = another member once ids are member ids (is the members file "
                "the export's?)]. Nothing was changed."
            )

    def member(self, person: str) -> str:
        """The member id of a raw id (a member id, "You" and "" stay as they are)."""
        return self.members.get(person, person)

    def moved(self, message: Message) -> Message:
        """A C1 message with its new time and member ids; everything else as it is."""
        out = {**message, "datetime": self.new_records[message["id"]]["datetime"],
               "sender": self.member(message["sender"])}
        quote = message.get("quote")
        if quote:
            out["quote"] = {**quote, "sender": self.member(quote["sender"])}
        if "reactions" in message:
            out["reactions"] = [
                {**r, "senders": [self.member(s) for s in r.get("senders") or []]}
                for r in message["reactions"]
            ]
        return out

    def thread_message(self, message: Message) -> Message:
        """A thread's message after the move: step 1's record of the new input when the
        thread holds step 1's record of the old one; otherwise the message itself, moved."""
        wa_id = message["id"]
        if message == self.old_records.get(wa_id):
            return self.new_records[wa_id]
        return self.moved(message)

    # ------------------------------------------------------------------ step 1

    def restamp_messages(self, inputs: Mapping[str, str], counts: Restamps) -> dict[str, str]:
        """Step 1's ``metadata.inputs`` (WhatsApp id -> fingerprint of its record)."""
        out = {}
        for wa_id, stored in inputs.items():
            old = self.old_records.get(wa_id)
            matched = old is not None and wa_id in self.place and stored == fingerprint(old)
            counts.add(matched)
            out[wa_id] = fingerprint(self.thread_message(old)) if matched else stored
        return out

    def move_thread(self, thread: Thread) -> Thread:
        unknown = [m["id"] for m in thread["messages"] if m["id"] not in self.place]
        if unknown:
            raise NotTheSameMessages(
                f"thread {thread['thread_id']} holds {len(unknown)} messages the pipeline input "
                f"does not have (e.g. {unknown[:3]}). Nothing was changed."
            )
        messages = sorted(map(self.thread_message, thread["messages"]),
                          key=lambda m: self.place[m["id"]])
        return {**thread, **thread_record(thread["thread_id"], messages)}

    def move_threads(self, payload: Payload, counts: Restamps) -> Payload:
        """A C1 threads file after the move: same threads and memberships, messages in chat
        order, threads by start time then the chat place of their first message."""
        threads = sorted(
            map(self.move_thread, payload["threads"]),
            key=lambda t: (t["start_time"], self.place[t["messages"][0]["id"]]),
        )
        metadata = payload.get("metadata", {})
        if "inputs" in metadata:
            metadata = {**metadata, "inputs": self.restamp_messages(metadata["inputs"], counts)}
        return {**payload, "threads": threads, "metadata": metadata}

    # ------------------------------------------------------------------ step 2

    @staticmethod
    def move_classified(
        payload: Payload,
        settings: Mapping[str, Any],
        old_threads: Mapping[str, Thread],
        new_threads: Mapping[str, Thread],
        counts: Restamps,
    ) -> Payload:
        """Step 2's file with ``metadata.inputs`` and every ``manual_fingerprint`` re-stamped;
        labels and probabilities as they are."""

        def restamp(thread_id: str, stored: str, fingerprint_of: Any) -> str:
            matched = thread_id in old_threads and stored == fingerprint_of(old_threads[thread_id])
            counts.add(matched)
            return fingerprint_of(new_threads[thread_id]) if matched else stored

        def step_fingerprint(thread: Thread) -> str:
            return input_fingerprint(thread, dict(settings))

        records = [
            {**r, MANUAL_FINGERPRINT: restamp(r["thread_id"], r[MANUAL_FINGERPRINT], content_fingerprint)}
            if MANUAL_FINGERPRINT in r else r
            for r in payload["threads"]
        ]
        metadata = payload.get("metadata", {})
        if "inputs" in metadata:
            inputs = {tid: restamp(tid, fp, step_fingerprint) for tid, fp in metadata["inputs"].items()}
            metadata = {**metadata, "inputs": inputs}
        return {**payload, "threads": records, "metadata": metadata}

    @staticmethod
    def stored_settings(classified: Payload) -> dict[str, Any]:
        metadata = classified.get("metadata", {})
        return {key: metadata.get(key) for key in SETTINGS_KEYS}

    # ------------------------------------------------------------------ step 3

    def move_claims(
        self,
        payload: Payload,
        audit: Sequence[dict[str, Any]],
        threads: tuple[Mapping[str, Thread], Mapping[str, Thread]],
        indexes: tuple[ExportIndex, ExportIndex],
        counts: Restamps,
    ) -> ClaimsMove:
        """Step 3's claims and audit after the move.

        ``threads`` / ``indexes``: the conversations step 3 reads and the export index of the
        input, before and after. Each stored fingerprint (a conversation's in
        ``metadata.inputs``, a message's in ``metadata.message_fingerprints``) is re-stamped
        where it matched. The claims of a conversation whose own fingerprint matched get their
        derived fields from the step's own ``ThreadClaims.finalize_all`` (a member seen under
        two ids becomes one person); their ids, texts, entities and evidence must come out the
        same. Every other audit record only has its people turned into member ids.
        """
        old_threads, new_threads = threads
        metadata = payload["metadata"]
        by_thread: dict[str, list[dict[str, Any]]] = {}
        for claim in payload["claims"]:
            by_thread.setdefault(claim["thread_id"], []).append(claim)
        current = {
            tid: (with_export(old_threads[tid], indexes[0]), with_export(new_threads[tid], indexes[1]))
            for tid in {*metadata.get("inputs", {}), *metadata.get("message_fingerprints", {})}
            if tid in old_threads and tid in new_threads
        }
        inputs, matched = {}, set()
        for tid, stored in metadata.get("inputs", {}).items():
            if tid in current and stored == claims_fingerprint(current[tid][0], self.providers):
                matched.add(tid)
            counts.add(tid in matched)
            inputs[tid] = (
                claims_fingerprint(current[tid][1], self.providers) if tid in matched else stored
            )
        fingerprints = {
            tid: self._restamp_message_fingerprints(stored, *current[tid]) if tid in current else stored
            for tid, stored in metadata.get("message_fingerprints", {}).items()
        }
        refreshed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        result = ClaimsMove(
            payload={**payload, "metadata": {**metadata, "inputs": inputs,
                                             "message_fingerprints": fingerprints}},
            audit=[],
        )
        for tid, claims in by_thread.items():
            if tid not in matched:
                continue
            pairs, lost = ThreadClaims(current[tid][1]).finalize_all(claims)
            if lost:
                raise NotTheSameMessages(
                    f"thread {tid}: {lost} claims would lose all their evidence. Nothing was "
                    "changed."
                )
            refreshed.update((claim["claim_id"], (claim, record)) for claim, record in pairs)
            result.refreshed_threads += 1
        claims = []
        for claim in payload["claims"]:
            fresh = refreshed.get(claim["claim_id"], (claim, None))[0]
            self._require_same_claim(claim, fresh)
            if any(claim.get(key) != fresh.get(key) for key in DERIVED_KEYS
                   if key not in ("supporting_message_ids", "opposing_message_ids")):
                result.counts_changed.append(claim["claim_id"])
            if any(claim.get(key) != fresh.get(key)
                   for key in ("supporting_message_ids", "opposing_message_ids")):
                result.order_changed += 1
            claims.append(fresh)
        result.payload["claims"] = claims
        result.audit = [
            refreshed[record["claim_id"]][1] if record["claim_id"] in refreshed
            else self._moved_audit(record)
            for record in audit
        ]
        return result

    def _restamp_message_fingerprints(
        self, stored: Mapping[str, str], old: Thread, new: Thread
    ) -> dict[str, str]:
        before = message_fingerprints(old, self.providers)
        after = message_fingerprints(new, self.providers)
        return {i: after[i] if i in after and before.get(i) == fp else fp for i, fp in stored.items()}

    @staticmethod
    def _require_same_claim(claim: dict[str, Any], fresh: dict[str, Any]) -> None:
        kept = {k: v for k, v in claim.items() if k not in DERIVED_KEYS}
        if kept != {k: v for k, v in fresh.items() if k not in DERIVED_KEYS} or any(
            set(claim.get(key) or []) != set(fresh.get(key) or [])
            for key in ("supporting_message_ids", "opposing_message_ids", "counters")
        ):
            raise NotTheSameMessages(
                f"claim {claim['claim_id']} would change beyond its counts and the order of its "
                "evidence. Nothing was changed."
            )

    def _moved_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        def people(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{**event, "person": self.member(event["person"])} for event in events]

        return {
            **record,
            "supporters": people(record.get("supporters") or []),
            "opposers": people(record.get("opposers") or []),
            "participants": sorted({self.member(p) for p in record.get("participants") or []}),
        }

    # ------------------------------------------------------------------ the gold file

    def move_gold(self, gold: Payload) -> Payload:
        """The gold file with its id lists in the new chat order and each conversation's
        ``start`` the new time of its first message; every label and membership as it is."""
        last = len(self.place)

        def in_order(ids: Sequence[str]) -> list[str]:
            return sorted(ids, key=lambda wa_id: self.place.get(wa_id, last))

        windows = [
            {**w, **{key: in_order(w[key]) for key in ("message_ids", "context_message_ids") if key in w}}
            for w in gold.get("windows", [])
        ]
        conversations = []
        for conversation in gold.get("conversations", []):
            times = [self.new_records[i]["datetime"] for i in conversation["message_ids"]
                     if self.new_records.get(i) is not None]
            start = min(times) if len(times) == len(conversation["message_ids"]) else conversation["start"]
            conversations.append({**conversation, "start": start,
                                  "message_ids": in_order(conversation["message_ids"])})
        moved = {**gold, "windows": windows}
        if "conversations" in gold:
            moved["conversations"] = conversations
        return moved
