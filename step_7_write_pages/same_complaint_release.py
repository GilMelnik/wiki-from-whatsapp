"""The same-complaint check, read side: which withheld claims a second person's same complaint
releases. Recomputed on every read; nothing derived is stored.

The rule (``pages.withhold``): a claim record (C6) whose stance is ``negative``, that names at
least one entity and is backed by fewer than ``min_supporters`` (2) different people is
withheld from every page (``withheld_by_rule``). The check (``step_7_write_pages.same_complaint``)
asks a model, entity by entity, which of the negative claims naming the entity make the same
complaint, and stores its groups (``SAME_COMPLAINT_PATH``).

A claim the rule withholds is RELEASED when, in at least one entity's list, a usable stored
answer put it in a group whose claims are backed by at least ``min_supporters`` different
people together. "Backed" is the record's own ``supporter_count`` rule, with the identities
read from the private audit (``claim_backers``), in memory only: one person complaining twice
releases nothing. A claim the owner marked "keep withheld" on the Withheld page
(``SAME_COMPLAINT_VETOES_PATH``) is never released and backs no other claim of its group.

Every doubt ends in "stays withheld":
- an entity's list (``entity_lists``) is its negative claims, in the order of the claims file;
  a stored answer counts only while the list has the fingerprint it was given for
  (``list_fingerprint``: short ids and texts, in order); a missing file or a stale answer
  releases nothing of that entity;
- a stored answer with a ``problem`` (the model's answer was unusable) or one that does not
  fit the list (an unknown id, a claim in two groups, a group of fewer than two claims) releases
  nothing of that entity and is logged.

Files (private, 2-space JSON):
- answers: ``{"entities": {entity_id: {"fingerprint": sha1, "groups": [{"label": English
  text, "claims": [short_id, ...]}], "problem": str | null, "answered_at": iso}},
  "metadata": {...}}``; no claim text, no person;
- vetoes: ``{"keep_withheld": [short_id, ...], "metadata": {"count", "updated_at"}}``,
  written by the Withheld page only (``save_vetoes``), never by a run.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_6_plan.claim_records import Record, claim_backers
from utils.config import section
from utils.json_io import read_json_if_present, write_json_file
from utils.paths import AUDIT_PATH, SAME_COMPLAINT_PATH, SAME_COMPLAINT_VETOES_PATH
from utils.reconcile import fingerprint
from utils.timestamps import utc_now

logger = logging.getLogger(__name__)

WITHHOLD_CONFIG = section("pages")["withhold"]
MIN_SUPPORTERS = int(WITHHOLD_CONFIG["min_supporters"])

# The state of an entity's list (``Check.state``).
NO_CALL = "no_call"  # nothing to release: no withheld claim, or fewer than two people in all
WAITING = "waiting"  # the check has no answer for today's list (never asked, or the list changed)
UNUSABLE = "unusable"  # the stored answer does not fit the list: nothing released
ANSWERED = "answered"


def withheld_by_rule(record: Mapping[str, Any]) -> bool:
    """The rule of ``pages.withhold`` on a claim record: a negative claim that names an entity
    and is backed by fewer than ``min_supporters`` people."""

    return (record["stance"] == WITHHOLD_CONFIG["stance"]
            and bool(record["entity_ids"])
            and (record["supporter_count"] or 0) < MIN_SUPPORTERS)


def entity_lists(records: Iterable[Record]) -> dict[str, list[Record]]:
    """``entity_id -> the negative claims that name it``, in the order of ``records``."""

    lists: dict[str, list[Record]] = {}
    for record in records:
        if record["stance"] == WITHHOLD_CONFIG["stance"]:
            for entity_id in record["entity_ids"]:
                lists.setdefault(entity_id, []).append(record)
    return lists


def entity_name(entity_id: str, claims: Sequence[Record]) -> str:
    """The canonical name of ``entity_id``, from a claim of its list."""

    record = claims[0]
    return record["entity_names"][record["entity_ids"].index(entity_id)]


def list_fingerprint(claims: Sequence[Record]) -> str:
    """What an answer was given for: the claims' short ids and texts, in order."""

    return fingerprint([[claim["short_id"], claim["claim_text"]] for claim in claims])


def people_of(short_ids: Iterable[str], backers: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """The different people backing any of ``short_ids`` (identities: memory only)."""

    return frozenset().union(*(backers.get(short_id, frozenset()) for short_id in short_ids))


def needs_call(claims: Sequence[Record], backers: Mapping[str, frozenset[str]]) -> bool:
    """An answer about this list could release something: it holds a claim the rule withholds
    and its claims are backed by at least ``min_supporters`` people in all."""

    return (any(withheld_by_rule(claim) for claim in claims)
            and len(people_of((c["short_id"] for c in claims), backers)) >= MIN_SUPPORTERS)


def groups_problem(groups: Any, ids: Collection[str]) -> str | None:
    """Why ``groups`` (``[{"label", "claims": [short_id]}]``) is not a usable answer about a
    list holding ``ids``; ``None`` when it is."""

    if not isinstance(groups, list):
        return "the groups are not a list"
    seen: set[str] = set()
    for group in groups:
        claims = group.get("claims") if isinstance(group, dict) else None
        if (not isinstance(claims, list) or not all(isinstance(c, str) for c in claims)
                or not isinstance(group.get("label"), str)):
            return "a group is not {label, claims: [id, ...]}"
        unknown = [c for c in claims if c not in ids]
        if unknown:
            return f"{unknown[0]!r} is not a claim of this list"
        if len(set(claims)) != len(claims):
            return "a claim is listed twice in one group"
        if len(claims) < 2:
            return "a group holds fewer than two claims"
        twice = seen & set(claims)
        if twice:
            return f"{sorted(twice)[0]} is in two groups"
        seen |= set(claims)
    return None


def load_answers(path: Path = SAME_COMPLAINT_PATH) -> dict[str, Any]:
    """The stored answers per entity id; ``{}`` when the check never ran."""

    payload = read_json_if_present(path)
    if payload is None:
        return {}
    entities = payload.get("entities") if isinstance(payload, dict) else None
    if not isinstance(entities, dict):
        raise ValueError(f'{path} is not the same-complaint answers file: expected {{"entities": '
                         '{entity_id: {fingerprint, groups, problem}}, "metadata": {...}}')
    return entities


def load_vetoes(path: Path = SAME_COMPLAINT_VETOES_PATH) -> frozenset[str]:
    """The short ids the owner marked "keep withheld"; empty when there is no file yet. A file
    that cannot be read raises: ignoring it would release what the owner kept back."""

    payload = read_json_if_present(path)
    if payload is None:
        return frozenset()
    kept = payload.get("keep_withheld") if isinstance(payload, dict) else None
    if not isinstance(kept, list) or not all(isinstance(short_id, str) for short_id in kept):
        raise ValueError(f'{path} is not the "keep withheld" file: expected '
                         '{"keep_withheld": [short_id, ...], "metadata": {...}}')
    return frozenset(kept)


def save_vetoes(vetoes: Collection[str], path: Path = SAME_COMPLAINT_VETOES_PATH) -> None:
    """Replace the "keep withheld" file atomically (the caller holds its lock)."""

    kept = sorted(set(vetoes))
    write_json_file({"keep_withheld": kept,
                     "metadata": {"count": len(kept), "updated_at": utc_now()}}, path)


@dataclass(frozen=True)
class Group:
    """One usable group of an entity's stored answer, judged against today's records."""

    entity_id: str
    label: str
    claims: tuple[str, ...]  # short ids, in the order of the entity's list
    people: int  # different people backing the claims not kept withheld
    people_unmarked: int  # the same, before the owner's "keep withheld" marks
    released: tuple[str, ...]  # the claims this group releases


class Check:
    """The check over one set of claim records: lists, the state of each, the usable groups and
    what they release (see the module docstring)."""

    def __init__(self, records: Sequence[Record], backers: Mapping[str, frozenset[str]],
                 answers: Mapping[str, Any], vetoes: frozenset[str]) -> None:
        self.records = {record["short_id"]: record for record in records}
        self.backers = backers
        self.vetoes = vetoes
        self.lists = entity_lists(records)
        self.states: dict[str, str] = {}
        self.problems: dict[str, str] = {}
        self.groups: list[Group] = []
        for entity_id, claims in self.lists.items():
            self.states[entity_id] = self._judge(entity_id, claims, answers.get(entity_id))
        self.released: dict[str, list[Group]] = {}
        for group in self.groups:
            for short_id in group.released:
                self.released.setdefault(short_id, []).append(group)

    @classmethod
    def load(cls, records: Sequence[Record], *, audit: Path = AUDIT_PATH,
             answers: Path = SAME_COMPLAINT_PATH,
             vetoes: Path = SAME_COMPLAINT_VETOES_PATH) -> Check:
        return cls(records, claim_backers(records, audit), load_answers(answers),
                   load_vetoes(vetoes))

    def asked(self) -> dict[str, list[Record]]:
        """The lists an answer could release something from (the ones the check asks about)."""

        return {e: claims for e, claims in self.lists.items() if needs_call(claims, self.backers)}

    def _judge(self, entity_id: str, claims: Sequence[Record], entry: Any) -> str:
        fresh = isinstance(entry, dict) and entry.get("fingerprint") == list_fingerprint(claims)
        if not fresh:
            return WAITING if needs_call(claims, self.backers) else NO_CALL
        problem = entry.get("problem") or groups_problem(entry.get("groups"),
                                                         [c["short_id"] for c in claims])
        if problem:
            self.problems[entity_id] = str(problem)
            logger.warning(f"Same-complaint answer for entity {entity_id} is unusable "
                           f"({problem}); none of its claims is released")
            return UNUSABLE
        order = {claim["short_id"]: k for k, claim in enumerate(claims)}
        for raw in entry["groups"]:
            members = tuple(sorted(raw["claims"], key=order.__getitem__))
            kept = [short_id for short_id in members if short_id not in self.vetoes]
            people = len(people_of(kept, self.backers))
            released = tuple(s for s in kept if withheld_by_rule(self.records[s])
                             ) if people >= MIN_SUPPORTERS else ()
            self.groups.append(Group(entity_id, raw["label"], members, people,
                                     len(people_of(members, self.backers)), released))
        return ANSWERED


def released_ids(records: Sequence[Record], *, audit: Path = AUDIT_PATH,
                 answers: Path = SAME_COMPLAINT_PATH,
                 vetoes: Path = SAME_COMPLAINT_VETOES_PATH) -> frozenset[str]:
    """The short ids of the claims the rule withholds that the check releases today (the
    owner's "keep withheld" marks applied). Part of what ``withhold`` decides."""

    return frozenset(Check.load(records, audit=audit, answers=answers, vetoes=vetoes).released)
