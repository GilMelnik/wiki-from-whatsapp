"""The kind of each entity: a person, an organisation, a place or something else.

Step 7 withholds a lone negative report about a named individual (``pages.withhold``), so it
must know which entities are people. Step 4 asks the cheap classifier of stage ``entities``
(contract C4, one ``choice`` question, ``entities.kind`` in ``config.json``) about every entity
whose kind is missing or out of date, and stores on the entity:

- ``kind``: one of ``entities.kind.kinds`` (``person``, ``organisation``, ``place``, ``other``);
- ``kind_confidence``: the classifier's probability of its top answer;
- ``kind_input``: the fingerprint of what the classifier read (the entity's names and how
  the claims mention it); when that changes (a new name, a merge) the kind is asked again;
- ``kind_manual``: ``true`` once the owner chose the kind in the entities tool. The owner's
  choice always wins: step 4 never asks about that entity again.

When in doubt, the entity is a person: a top answer below ``entities.kind.min_confidence``
becomes ``entities.kind.when_unsure``, and an entity with no kind at all (a file written before
kinds existed, a failed classification, a merge of two different kinds) counts as a person
for the rule (``step_7_write_pages.withhold``).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from utils.config import ENTITIES_CONFIG
from utils.llm_client import Classifier, ClassifierError, Question
from utils.reconcile import fingerprint

KIND_CONFIG = ENTITIES_CONFIG["kind"]
KINDS = tuple(KIND_CONFIG["kinds"])
UNKNOWN = "unknown"
QUESTION = Question("kind", "choice", KIND_CONFIG["question"],
                    options=tuple(KIND_CONFIG["kinds"].values()))
_KIND_OF_LABEL = {label: kind for kind, label in KIND_CONFIG["kinds"].items()}
_AUTO_FIELDS = ("kind", "kind_confidence", "kind_input")


def _excerpt(text: str, name: str) -> str:
    """The words around the first mention of ``name`` in ``text`` (the text's start if the
    claim tags the name without writing it)."""
    width = KIND_CONFIG["excerpt_chars"]
    text = " ".join(text.split())
    match = re.search(re.escape(name), text, flags=re.IGNORECASE)
    if match is None:
        return text[: 2 * width]
    return text[max(match.start() - width, 0): match.end() + width]


def kind_state(entity: Mapping[str, Any], claims_by_id: Mapping[str, Mapping[str, Any]]) -> str:
    """What the classifier reads about one entity: its name, its other spellings and a few
    excerpts of the claims that mention it (most-claimed names first)."""
    lines = [f"Name: {entity.get('canonical_name') or ''}"]
    others = [alias for alias in entity.get("aliases") or [] if alias != entity.get("canonical_name")]
    if others:
        lines.append(f"Other spellings: {', '.join(others)}")
    excerpts: list[str] = []
    for member in entity.get("members") or []:
        for claim_id in member.get("sample_claim_ids") or []:
            claim = claims_by_id.get(claim_id)
            if claim is not None and len(excerpts) < KIND_CONFIG["max_excerpts"]:
                excerpts.append(_excerpt(claim.get("claim_text") or "", member["name"]))
    if excerpts:
        lines.append("How the members' messages mention it:")
        lines += [f"- {excerpt}" for excerpt in dict.fromkeys(excerpts)]
    return "\n".join(lines)


def pick_kind(probabilities: Mapping[str, float]) -> tuple[str, float]:
    """The kind of the top answer and its probability; a top answer below
    ``min_confidence`` is ``when_unsure`` (a person)."""
    label, probability = max(probabilities.items(), key=lambda item: item[1])
    kind = _KIND_OF_LABEL.get(label, KIND_CONFIG["when_unsure"])
    if probability < KIND_CONFIG["min_confidence"]:
        kind = KIND_CONFIG["when_unsure"]
    return kind, round(float(probability), 3)


def known_kind(entity: Mapping[str, Any]) -> str | None:
    """The entity's kind, or ``None`` when it has none (or one this version does not know)."""
    kind = entity.get("kind")
    return kind if kind in KINDS else None


def kind_counts(entities: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many entities have each kind (``unknown``: none yet), and how many were set by hand."""
    entities = list(entities)
    counts = Counter(known_kind(entity) or UNKNOWN for entity in entities)
    return {**{kind: counts[kind] for kind in (*KINDS, UNKNOWN)},
            "manual": sum(1 for entity in entities if entity.get("kind_manual"))}


def set_manual_kind(entity: dict[str, Any], kind: str) -> None:
    """The owner's choice: it wins over the classifier, now and on every later run."""
    if kind not in KINDS:
        raise ValueError(f"unknown entity kind {kind!r}; expected one of {KINDS}")
    for key in _AUTO_FIELDS:
        entity.pop(key, None)
    entity["kind"] = kind
    entity["kind_manual"] = True


def merge_kind(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """The kind of ``target`` after ``source`` was merged into it: the owner's choice on the
    target, else the owner's choice on the source, else the target's kind when both agree;
    two different automatic kinds leave the target without a kind (a person for the rule)
    until step 4 asks again."""
    if target.get("kind_manual"):
        return
    if source.get("kind_manual"):
        set_manual_kind(target, source["kind"])
    elif known_kind(source) != known_kind(target):
        for key in _AUTO_FIELDS:
            target.pop(key, None)


class EntityKinds:
    """Classifies the entities whose kind is missing or out of date (see the module docstring)."""

    def __init__(self, classifier: Classifier, claims_by_id: Mapping[str, Mapping[str, Any]],
                 logger: logging.Logger) -> None:
        self.classifier = classifier
        self.claims_by_id = claims_by_id
        self.logger = logger

    def due(self, entities: Iterable[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
        """``(entity, state)`` of every entity the owner did not decide whose kind is missing
        or was read from other names or mentions."""
        found = []
        for entity in entities:
            if entity.get("kind_manual"):
                continue
            state = kind_state(entity, self.claims_by_id)
            if known_kind(entity) is None or entity.get("kind_input") != fingerprint(state):
                found.append((entity, state))
        return found

    def apply(self, entities: list[dict[str, Any]]) -> dict[str, int]:
        """Ask about every due entity and store the answers; a failed request leaves the kinds
        as they are (logged; an entity without a kind counts as a person)."""
        due = self.due(entities)
        if not due:
            return {"kinds_classified": 0}
        self.logger.info(f"Entity kinds: asking the classifier about {len(due)} entities "
                         f"({sum(len(state) for _, state in due)} characters)")
        try:
            answers = self.classifier.classify_many([(state, [QUESTION]) for _, state in due],
                                                    task="entity_kind")
        except ClassifierError as exc:
            self.logger.warning(f"Entity kinds: classification failed ({exc}); {len(due)} entities "
                                "keep their kind or have none (no kind counts as a person); the "
                                "next run asks again")
            return {"kinds_classified": 0, "kinds_failed": len(due)}
        changed = 0
        for (entity, state), (answer,) in zip(due, answers):
            kind, confidence = pick_kind(answer.probabilities)
            changed += entity.get("kind") != kind
            entity.update(kind=kind, kind_confidence=confidence, kind_input=fingerprint(state))
        self.logger.info(f"Entity kinds: {len(due)} classified, {changed} changed; now "
                         f"{kind_counts(entities)}")
        return {"kinds_classified": len(due), "kinds_changed": changed}
