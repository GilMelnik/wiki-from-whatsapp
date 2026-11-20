"""The same-complaint check, read side: which withheld claims a second person's same complaint
releases, and so what is withheld from the wiki now. Recomputed on every read; nothing derived
is stored.

The rule (``step_5_same_complaint.withhold.withholds``, ``withhold`` in ``config.json``): a
claim record (C6) whose stance is ``negative``, that names at least one entity and is backed by fewer than
``min_supporters`` (2) different people is withheld from every page. The check
(``step_5_same_complaint.run``) asks a model, entity by entity, which of the negative
claims naming the entity make the same complaint, and stores its groups (``SAME_COMPLAINT_PATH``).

``withheld_ids(records, sources)`` is THE answer to "what is withheld now": the rule, minus what
the check releases (the owner's "keep withheld" marks applied), minus what the owner released by
hand. Every reader calls it once per load of the records (step 6's planning list, step 8's
``Corpus``, step 11, the Plan page); the Withheld page, which shows the check's details, reads
the same ``Check.withheld``. With no answers file (the check never ran) the check releases
nothing and the audit is not read; the owner's hand releases still apply.

A claim the rule withholds is RELEASED when, in at least one entity's list, a usable stored
answer put it in a group whose claims are backed by at least ``min_supporters`` different
people together. "Backed" is the record's own ``supporter_count`` rule, with the identities
read from the private audit (``claim_backers``), in memory only: one person complaining twice
releases nothing. A claim the owner marked "keep withheld" on the Withheld page
(``SAME_COMPLAINT_VETOES_PATH``) is never released and backs no other claim of its group.

The owner's other mark, "released" (the same page, one decision per claim), publishes a claim the
rule withholds whatever the check says. A released short id the rule does not withhold (the claim
gained a second backer, changed or is gone) changes nothing. The two marks exclude each other; a
short id found in both (a hand-edited file) counts as "keep withheld" only, with a warning.

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
- the owner's marks: ``{"keep_withheld": [short_id, ...], "released": [short_id, ...],
  "metadata": {"keep_withheld_count", "released_count", "updated_at"}}``, written by the
  Withheld page only (``save_marks``), never by a run; a file without ``released`` (written
  before hand releases existed) reads as releasing nothing by hand.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_5_same_complaint.claim_records import ClaimSources, Record, claim_backers
from step_5_same_complaint.withhold import MIN_SUPPORTERS, WITHHOLD_CONFIG, withholds
from utils.json_io import read_json_if_present, write_json_file
from utils.paths import SAME_COMPLAINT_PATH, SAME_COMPLAINT_VETOES_PATH
from utils.reconcile import fingerprint
from utils.timestamps import utc_now

logger = logging.getLogger(__name__)

# The state of an entity's list (``Check.state``).
NO_CALL = "no_call"  # nothing to release: no withheld claim, or fewer than two people in all
WAITING = "waiting"  # the check has no answer for today's list (never asked, or the list changed)
UNUSABLE = "unusable"  # the stored answer does not fit the list: nothing released
ANSWERED = "answered"


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

    return (any(withholds(claim) for claim in claims)
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


@dataclass(frozen=True)
class Marks:
    """The owner's marks of the Withheld page: the short ids kept withheld (the check never
    releases them) and the ones released by hand (published whatever the check says)."""

    keep_withheld: frozenset[str] = frozenset()
    released: frozenset[str] = frozenset()

    @property
    def hand_released(self) -> frozenset[str]:
        """The hand releases that count: a short id also kept withheld stays withheld."""

        return self.released - self.keep_withheld


def _short_ids(payload: dict[str, Any], key: str, path: Path) -> frozenset[str]:
    ids = payload.get(key, [])
    if not isinstance(ids, list) or not all(isinstance(short_id, str) for short_id in ids):
        raise ValueError(f'{path} is not the owner\'s marks file: "{key}" must be a list of short '
                         'ids; expected {"keep_withheld": [short_id, ...], "released": [short_id, '
                         '...], "metadata": {...}}')
    return frozenset(ids)


def load_marks(path: Path = SAME_COMPLAINT_VETOES_PATH) -> Marks:
    """The owner's marks; none when there is no file yet. A file that cannot be read raises:
    ignoring it would release what the owner kept back, or withhold what they released."""

    payload = read_json_if_present(path)
    if payload is None:
        return Marks()
    if not isinstance(payload, dict) or "keep_withheld" not in payload:
        raise ValueError(f'{path} is not the owner\'s marks file: expected {{"keep_withheld": '
                         '[short_id, ...], "released": [short_id, ...], "metadata": {...}}')
    marks = Marks(_short_ids(payload, "keep_withheld", path), _short_ids(payload, "released", path))
    both = marks.keep_withheld & marks.released
    if both:
        logger.warning(f"{path}: {len(both)} claim(s) are both kept withheld and released by "
                       f"hand ({sorted(both)}); they stay withheld")
    return marks


def save_marks(marks: Marks, path: Path = SAME_COMPLAINT_VETOES_PATH) -> None:
    """Replace the owner's marks file atomically (the caller holds its lock)."""

    kept, released = sorted(marks.keep_withheld), sorted(marks.released)
    write_json_file({"keep_withheld": kept, "released": released,
                     "metadata": {"keep_withheld_count": len(kept),
                                  "released_count": len(released), "updated_at": utc_now()}},
                    path)


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
                 answers: Mapping[str, Any], marks: Marks) -> None:
        self.records = {record["short_id"]: record for record in records}
        self.backers = backers
        self.marks = marks
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
        # What is withheld now: the rule, minus what the check released, minus the owner's
        # hand releases.
        self.withheld = frozenset(short_id for short_id, record in self.records.items()
                                  if withholds(record) and short_id not in self.released
                                  and short_id not in marks.hand_released)

    @classmethod
    def load(cls, records: Sequence[Record], sources: ClaimSources = ClaimSources()) -> Check:
        """The check over ``records`` with the audit, the answers and the marks of ``sources``."""

        return cls(records, claim_backers(records, sources.audit),
                   load_answers(sources.same_complaint), load_marks(sources.same_complaint_vetoes))

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
            kept = [short_id for short_id in members if short_id not in self.marks.keep_withheld]
            people = len(people_of(kept, self.backers))
            released = tuple(s for s in kept if withholds(self.records[s])
                             ) if people >= MIN_SUPPORTERS else ()
            self.groups.append(Group(entity_id, raw["label"], members, people,
                                     len(people_of(members, self.backers)), released))
        return ANSWERED


def withheld_ids(records: Sequence[Record], sources: ClaimSources = ClaimSources()
                 ) -> frozenset[str]:
    """The short ids of the claims withheld from the wiki now: the ones the rule withholds,
    minus the ones the check releases today (the owner's "keep withheld" marks applied), minus
    the ones the owner released by hand. ``sources`` names the audit, the answers and the marks
    (the ones ``records`` were loaded with). Call it once per load of the records."""

    by_rule = frozenset(record["short_id"] for record in records if withholds(record))
    if not by_rule:
        return by_rule
    marks = load_marks(sources.same_complaint_vetoes)
    answers = load_answers(sources.same_complaint)
    if not answers:  # the check never ran (or has nothing to say): it releases nothing
        return by_rule - marks.hand_released
    return Check(records, claim_backers(records, sources.audit), answers, marks).withheld
