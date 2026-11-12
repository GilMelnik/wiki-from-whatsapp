"""Turn the model's small JSON into C3 claims, and keep claims' derived fields current.

A claim record (C3) is ``{claim_id, thread_id, claim_text, entities, stance,
supporting_message_ids, opposing_message_ids, counters, first_date, last_date,
supporter_count, opposer_count, participant_count, reaction_summary, _redactions?}``. The model supplies text,
entities, stance, message numbers and the claims a claim disputes; everything in
``DERIVED_KEYS`` is computed or maintained here. A claim id is
``<thread_id>-<sha1[:8](text as extracted)>`` and is assigned once: later runs carry the record
and only recompute its derived fields.

``counters`` holds the ids of the claims of the same thread that this claim disputes (a member
answered another one). The model names them by the number ``n`` it gave each claim of its
answer (never by position; ``n`` is not stored). The link is mutual (if B counters A, A
counters B), is set only from the model's answers (a new claim may dispute an existing one in
update mode), and loses an id when that claim leaves the output. It is a derived key: a manual edit of the claim survives
when a later dissent adds a link to it.

``participant_count`` is the number of distinct people who wrote in the claim's thread (not
people who only reacted); the private audit keeps who they were (``participants``), so step 5
can count distinct people across a group's threads.

``ThreadClaims`` works on one thread whose messages already carry quotes and reactions
(``utils.threads_io.with_export``).
"""

from __future__ import annotations

import hashlib
from typing import Any

from step_3_extract.prompt import (
    EXTRACT_CONFIG,
    STANCES,
    build_fresh_prompt,
    build_update_prompt,
)
from utils.reconcile import fingerprint
from utils.support import collect_evidence, person_key
from utils.threads_io import render_thread_for_llm

# The fields the step always owns and recomputes; a manual edit of a claim keeps its text
# but these follow the thread (``utils.reconcile`` ``refresh_keys``).
DERIVED_KEYS = (
    "supporting_message_ids",
    "opposing_message_ids",
    "counters",
    "first_date",
    "last_date",
    "supporter_count",
    "opposer_count",
    "participant_count",
    "reaction_summary",
)


class OutputError(ValueError):
    """The model's JSON does not have the requested shape."""


def claim_id_for(thread_id: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{thread_id}-{digest[: int(EXTRACT_CONFIG['claim_id_hash_chars'])]}"


def message_fingerprints(thread: dict[str, Any]) -> dict[str, str]:
    """WhatsApp id -> short fingerprint of what step 3 reads from that message (no reactions)."""

    size = int(EXTRACT_CONFIG["message_fingerprint_chars"])
    return {
        str(m["id"]): fingerprint(
            [m.get("datetime"), m.get("sender"), m.get("content"), m.get("quote")]
        )[:size]
        for m in thread.get("messages", [])
    }


def input_fingerprint(thread: dict[str, Any]) -> str:
    """C14 input fingerprint of a thread: message ids + contents + reactions."""

    return fingerprint(
        {
            "messages": message_fingerprints(thread),
            "reactions": {str(m["id"]): m.get("reactions") for m in thread.get("messages", [])},
        }
    )


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise OutputError(f"model output does not match the schema: {detail}")


def _check_numbers(item: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    for key in keys:
        values = item.get(key)
        _require(
            isinstance(values, list)
            and all(isinstance(v, int) and not isinstance(v, bool) for v in values),
            f"{where}.{key} must be a list of integers",
        )


def validate_output(data: Any, *, update: bool) -> None:
    """Raise ``OutputError`` unless ``data`` has the shape of FRESH / UPDATE schema. A claim's
    ``n`` is not checked: a missing or wrong one only makes the claim unreachable by
    ``counters`` (``ThreadClaims._link_counters``) and never costs the whole answer."""

    _require(isinstance(data, dict), "top level must be an object")
    claims = data.get("claims")
    _require(isinstance(claims, list), "claims must be a list")
    for index, claim in enumerate(claims):
        where = f"claims[{index}]"
        _require(isinstance(claim, dict), f"{where} must be an object")
        _require(isinstance(claim.get("text"), str), f"{where}.text must be a string")
        entities = claim.get("entities")
        _require(
            isinstance(entities, list) and all(isinstance(e, str) for e in entities),
            f"{where}.entities must be a list of strings",
        )
        _require(claim.get("stance") in STANCES, f"{where}.stance must be one of {STANCES}")
        _check_numbers(claim, ("supporting", "opposing", "counters"), where)
        if update:
            _check_numbers(claim, ("counters_existing",), where)
    if update:
        existing = data.get("existing")
        _require(isinstance(existing, list), "existing must be a list")
        for index, entry in enumerate(existing):
            where = f"existing[{index}]"
            _require(isinstance(entry, dict), f"{where} must be an object")
            claim_ref = entry.get("claim")
            _require(
                isinstance(claim_ref, int) and not isinstance(claim_ref, bool),
                f"{where}.claim must be an integer",
            )
            _check_numbers(entry, ("supporting", "opposing"), where)


class ThreadClaims:
    """Claims of one thread: rendering for the model, number -> id mapping, derived fields.

    ``new_ids`` are the messages added since the thread was last processed; they are marked
    in the rendering and are the only evidence an update may add.
    """

    def __init__(self, thread: dict[str, Any], new_ids: frozenset[str] = frozenset()) -> None:
        self.thread_id = str(thread["thread_id"])
        self.new_ids = new_ids
        self.messages_by_id = {str(m["id"]): m for m in thread.get("messages", [])}
        self.participants = sorted(
            {person_key(m.get("sender")) for m in self.messages_by_id.values()} - {""}
        )
        self._position = {message_id: i for i, message_id in enumerate(self.messages_by_id)}
        self.rendered, self.refs = render_thread_for_llm(thread, new_ids=new_ids)
        self.ungrounded = 0  # new claims dropped for lacking (new) supporting evidence
        self.invalid_counters: list[str] = []  # dropped ``counters`` references, explained

    def prompt(self, existing: list[dict[str, Any]]) -> str:
        if existing:
            new = sum(1 for message_id in self.refs if message_id in self.new_ids)
            return build_update_prompt(self.rendered, [c["claim_text"] for c in existing], new)
        return build_fresh_prompt(self.rendered, len(self.refs))

    def _ordered(self, ids: set[str]) -> list[str]:
        return sorted(ids, key=self._position.__getitem__)

    def _ids(self, numbers: list[int], *, only_new: bool) -> set[str]:
        """WhatsApp ids of the shown message numbers; out-of-range numbers are dropped."""

        ids = {self.refs[n - 1] for n in numbers if 1 <= n <= len(self.refs)}
        return ids & self.new_ids if only_new else ids

    def absorb(
        self, data: Any, existing: list[dict[str, Any]], *, require_evidence: bool = True
    ) -> list[dict[str, Any]]:
        """Apply one model answer: ``existing`` (records, never re-texted) gain the evidence
        the answer gives them, new claims are appended. Returns records whose derived fields
        are still to be computed by ``finalize``. Raises ``OutputError`` on a malformed answer.

        A new claim without supporting messages is dropped (counted in ``ungrounded``) unless
        ``require_evidence`` is off, which only the v1 migration uses for claims the old model
        did not cite.
        """

        update = bool(existing)
        validate_output(data, update=update)
        records = [dict(c) for c in existing]
        by_id = {c["claim_id"]: c for c in records}
        existing_ids = set(by_id)
        resolved: list[dict[str, Any] | None] = []  # the record of each answered claim
        for entry in data.get("existing", []) if update else []:
            if 1 <= entry["claim"] <= len(records):
                self._add_evidence(
                    records[entry["claim"] - 1],
                    self._ids(entry["supporting"], only_new=True),
                    self._ids(entry["opposing"], only_new=True),
                )
        for raw in data["claims"]:
            text = raw["text"].strip()
            supporting = self._ids(raw["supporting"], only_new=False)
            opposing = self._ids(raw["opposing"], only_new=False) - supporting
            if (
                not text
                or (require_evidence and not supporting)
                or (update and not supporting & self.new_ids)
            ):
                self.ungrounded += 1
                resolved.append(None)
                continue
            claim_id = claim_id_for(self.thread_id, text)
            if claim_id in by_id:  # the same text again: one claim, more evidence
                if claim_id in existing_ids:  # an existing claim gains new messages only
                    supporting, opposing = supporting & self.new_ids, opposing & self.new_ids
                self._add_evidence(by_id[claim_id], supporting, opposing)
                resolved.append(by_id[claim_id])
                continue
            record: dict[str, Any] = {
                "claim_id": claim_id,
                "thread_id": self.thread_id,
                "claim_text": text,
                "entities": list(dict.fromkeys(e.strip() for e in raw["entities"] if e.strip())),
                "stance": raw["stance"],
                "supporting_message_ids": self._ordered(supporting),
                "opposing_message_ids": self._ordered(opposing),
                "counters": [],
            }
            by_id[claim_id] = record
            records.append(record)
            resolved.append(record)
        self._link_counters(data["claims"], resolved, records[: len(existing)])
        return records

    def _link_counters(
        self,
        raws: list[dict[str, Any]],
        resolved: list[dict[str, Any] | None],
        existing: list[dict[str, Any]],
    ) -> None:
        """Turn each answered claim's ``counters`` (``n`` values of claims of the answer) and
        ``counters_existing`` (numbers of existing claims) into mutual links between records.
        A reference to no claim, to a dropped claim, to an ``n`` that several claims carry or
        to the claim itself is dropped and explained in ``invalid_counters``. A claim whose
        ``n`` is missing or not a positive integer is kept but no reference can reach it."""

        by_n: dict[int, list[int]] = {}
        for index, raw in enumerate(raws):
            n = raw.get("n")
            if isinstance(n, int) and not isinstance(n, bool) and n >= 1:
                by_n.setdefault(n, []).append(index)
        missing = "no such claim (or it was dropped)"
        for index, (raw, record) in enumerate(zip(raws, resolved)):
            targets = [
                ("counters", ref, *_numbered(by_n.get(ref, []), resolved, missing))
                for ref in raw["counters"]
            ] + [
                ("counters_existing", ref, existing[ref - 1] if 1 <= ref <= len(existing) else None,
                 missing)
                for ref in raw.get("counters_existing") or []
            ]
            for field, ref, target, problem in targets:
                where = f"claims[{index}].{field} {ref}"
                if record is None:
                    self.invalid_counters.append(f"{where}: the claim itself was dropped")
                elif target is None:
                    self.invalid_counters.append(f"{where}: {problem}")
                elif target is record:
                    self.invalid_counters.append(f"{where}: a claim cannot dispute itself")
                else:
                    _link(record, target)

    def _add_evidence(
        self, record: dict[str, Any], supporting: set[str], opposing: set[str]
    ) -> None:
        old_supporting = set(record.get("supporting_message_ids") or [])
        old_opposing = set(record.get("opposing_message_ids") or [])
        new_supporting = old_supporting | (supporting - old_opposing)
        record["supporting_message_ids"] = self._ordered(new_supporting)
        record["opposing_message_ids"] = self._ordered(old_opposing | (opposing - new_supporting))

    def finalize(self, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """``(claim, audit)`` with derived fields recomputed from the thread, or ``None`` when
        none of the claim's supporting messages is in the thread any more.

        Evidence ids of messages that left the thread are dropped; every other field of the
        record (id, text, entities, stance, manual markers) is kept as it is. A claim that never
        had evidence (a migrated v1 claim the old model did not cite) is kept, dated by the
        span of its thread, with zero counts.
        """

        supporting = {i for i in record.get("supporting_message_ids") or [] if i in self._position}
        opposing = {
            i for i in record.get("opposing_message_ids") or [] if i in self._position
        } - supporting
        if not supporting and record.get("supporting_message_ids"):
            return None
        evidence = collect_evidence(
            self.messages_by_id, self._ordered(supporting), self._ordered(opposing)
        )
        span = [str(m.get("datetime") or "")[:10] for m in self.messages_by_id.values()]
        span = [day for day in span if day]
        derived = {
            "supporting_message_ids": self._ordered(supporting),
            "opposing_message_ids": self._ordered(opposing),
            "counters": list(record.get("counters") or []),
            "first_date": evidence.first_date or (min(span) if span else None),
            "last_date": evidence.last_date or (max(span) if span else None),
            "supporter_count": evidence.supporter_count,
            "opposer_count": evidence.opposer_count,
            "participant_count": len(self.participants),
            "reaction_summary": evidence.reaction_summary,
        }
        head = {k: v for k, v in record.items() if k not in DERIVED_KEYS and not k.startswith("_")}
        tail = {k: v for k, v in record.items() if k.startswith("_")}
        claim = {**head, **derived, **tail}
        audit = {
            "claim_id": record["claim_id"],
            "thread_id": self.thread_id,
            "supporters": evidence.supporters,
            "opposers": evidence.opposers,
            "participants": self.participants,
        }
        return claim, audit

    def finalize_all(
        self, records: list[dict[str, Any]]
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
        """``finalize`` every record of the thread; ``counters`` then keep only claims that
        are still in the output. Returns the ``(claim, audit)`` pairs and the number of
        claims dropped."""

        kept = [pair for pair in map(self.finalize, records) if pair is not None]
        ids = {claim["claim_id"] for claim, _ in kept}
        for claim, _ in kept:
            claim["counters"] = [cid for cid in claim["counters"] if cid in ids]
        return kept, len(records) - len(kept)


def _numbered(
    indices: list[int], resolved: list[dict[str, Any] | None], missing: str
) -> tuple[dict[str, Any] | None, str]:
    """The record of the one answered claim at ``indices`` (the claims carrying one ``n``),
    or ``None`` and why there is none."""

    if len(indices) > 1:
        return None, f"{len(indices)} claims of the answer have this n"
    return (resolved[indices[0]], missing) if indices else (None, missing)


def _link(a: dict[str, Any], b: dict[str, Any]) -> None:
    """Make ``a`` and ``b`` counter each other (new lists: records are shallow copies)."""

    for one, other in ((a, b), (b, a)):
        ids = list(one.get("counters") or [])
        if other["claim_id"] not in ids:
            one["counters"] = [*ids, other["claim_id"]]


def check_counters(claims: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Where the opposing evidence and the counter links of one thread's claims disagree
    (not an error: logged and counted). Keys:

    - ``opposed_without_counter_claim``: claims with opposing messages but no counter claim
      (a bare count; right when the dissent said nothing beyond "no");
    - ``opposing_not_in_counter``: claims with counters some of whose opposing messages are in
      the supporting of none of them;
    - ``link_without_shared_message``: ``"<a> <b>"`` pairs linked by counters where neither
      claim's opposing messages are among the other's supporting.
    """

    by_id = {c["claim_id"]: c for c in claims}
    found: dict[str, list[str]] = {
        "opposed_without_counter_claim": [],
        "opposing_not_in_counter": [],
        "link_without_shared_message": [],
    }
    for claim in claims:
        opposing = set(claim["opposing_message_ids"])
        others = [by_id[cid] for cid in claim["counters"] if cid in by_id]
        answered = {m for other in others for m in other["supporting_message_ids"]}
        if opposing and not others:
            found["opposed_without_counter_claim"].append(claim["claim_id"])
        elif opposing - answered and others:
            found["opposing_not_in_counter"].append(claim["claim_id"])
        for other in others:
            if claim["claim_id"] < other["claim_id"] and not (
                opposing & set(other["supporting_message_ids"])
                or set(other["opposing_message_ids"]) & set(claim["supporting_message_ids"])
            ):
                found["link_without_shared_message"].append(
                    f"{claim['claim_id']} {other['claim_id']}"
                )
    return found
