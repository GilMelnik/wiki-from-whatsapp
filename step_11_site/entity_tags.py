"""Entity tags: a tag page for every often-named entity the plan does not cover (the owner's rule).

An entity (step 4; any review status, people included) gets a tag when ALL hold:

1. at least ``site.entity_tags.min_claims`` claim records name it (``entity_ids``);
2. at least ``min_pages`` published pages cite a claim that names it. A page cites the claims of
   its points (C9 ``points[].claims``); a withheld claim never counts, and a left-out claim is
   not in the points, so a tag never lists a page that does not mention the entity and never
   shows that a claim was withheld;
3. the plan does not cover it already. Every name of the entity (its canonical name and its
   aliases) is compared with every name of the plan's pages (title and aliases) and labels
   (title), all normalised by step 4's ``normalize_name``:

   - equal names: covered, no tag;
   - a partial match: the words of the shorter name appear, in order and next to each other,
     among the words of the longer one, where a word may carry a Hebrew prefix letter
     (``mcp.search.hebrew_prefixes``, keeping ``min_stem_length`` letters), and the shorter
     name has at least ``min_stem_length`` characters. Such an entity is uncertain: the owner
     decides on the Plan page (``covered`` or ``tag``, ``ENTITY_TAG_DECISIONS_PATH``); until
     then it gets no tag;
   - no match: not covered.

``entity_tags(...)`` is the rule as one pure function. Its pages come from what each page
cites: the written pages (``cited_claims``), or, before step 7 has run, an estimate from the
assignments (``assigned_claims``). The tag's id is ``id_prefix`` + the entity id: the prefix
holds a character a plan slug may not hold, so a tag id never equals a label id. A tag page
shows the entity's canonical name and the list of pages; nothing from the claims.

``python -m step_11_site.entity_tags --report`` logs the numbers: entities with at least N
claims for every N of ``report_min_claims``, then, for the current thresholds, how many are
covered, uncertain, on too few pages and tagged. No model is called.
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_4_entities.normalize import normalize_name
from step_5_same_complaint.claim_records import ClaimSources, Record, load_claim_records
from step_5_same_complaint.release import withheld_ids
from step_6_plan.model import iter_nodes
from utils.config import section
from utils.json_io import read_json_file, read_json_if_present, write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import (
    ASSIGNMENTS,
    ENTITY_TAG_DECISIONS_PATH,
    STEP_11,
    resolve,
    resolve_entities_path,
    resolve_plan_path,
    resolve_wiki_pages_path,
)
from utils.timestamps import utc_now

logger = logging.getLogger(__name__)

TAGS_CONFIG = section("site")["entity_tags"]
SEARCH_CONFIG = section("mcp")["search"]
ID_PREFIX = TAGS_CONFIG["id_prefix"]
TAG_KIND = TAGS_CONFIG["kind"]
COVERED, TAG = "covered", "tag"
DECISIONS = (COVERED, TAG)
# What happened to an entity named by enough claims, in the order the rule decides it.
FEW_PAGES, UNCERTAIN, TAGGED = "few_pages", "uncertain", "tagged"
STATUSES = (FEW_PAGES, COVERED, UNCERTAIN, TAGGED)
# Where the pages of a run come from.
FROM_PAGES, FROM_ASSIGNMENTS, NO_PAGES = "pages", "assignments", "none"


def tag_id(entity_id: str) -> str:
    """The id (and file name, under ``site.labels_dir``) of an entity's tag."""
    return f"{ID_PREFIX}{entity_id}"


# ------------------------------------------------------------------------------- inputs


def entity_names(entities: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{entity_id: [canonical name, *aliases]}`` of a step-4 entities payload."""
    names = {}
    for entity in entities.get("entities") or []:
        canonical = entity.get("canonical_name") or entity["entity_id"]
        names[entity["entity_id"]] = list(dict.fromkeys([canonical, *(entity.get("aliases") or [])]))
    return names


def cited_claims(pages: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """``{page_id: short ids}`` the written pages (C9) cite in their points."""
    return {
        page_id: {short_id for point in page.get("points") or [] for short_id in point["claims"]}
        for page_id, page in pages.items()
    }


def assigned_claims(assignments: Mapping[str, Any]) -> dict[str, set[str]]:
    """``{page_id: short ids}`` the assignments (C8) put on each page: the pages' estimate."""
    out: dict[str, set[str]] = {}
    for short_id, record in (assignments.get("assignments") or {}).items():
        for page_id in record.get("pages") or []:
            out.setdefault(page_id, set()).add(short_id)
    return out


def load_decisions(path: Path = ENTITY_TAG_DECISIONS_PATH) -> dict[str, str]:
    """The owner's decisions, ``{}`` when there is no file yet."""
    payload = read_json_if_present(path)
    if payload is None:
        return {}
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(decisions, dict) or not all(value in DECISIONS for value in decisions.values()):
        raise ValueError(
            f"{path} is not the entity tag decisions file: expected "
            f'{{"decisions": {{"<entity_id>": "covered" | "tag"}}, "metadata": {{...}}}}'
        )
    return dict(decisions)


def write_decisions(decisions: Mapping[str, str], path: Path = ENTITY_TAG_DECISIONS_PATH) -> None:
    """Write the decisions atomically; the caller holds the file's lock."""
    wrong = {entity_id: value for entity_id, value in decisions.items() if value not in DECISIONS}
    if wrong:
        raise ValueError(f"an entity tag decision is one of {DECISIONS}, got {wrong}")
    ordered = dict(sorted(decisions.items()))
    write_json_file({"decisions": ordered, "metadata": {"decision_count": len(ordered),
                                                         "updated_at": utc_now()}}, path)


# ----------------------------------------------------------------------------- matching


def _variants(word: str) -> set[str]:
    """The word and its stems without one Hebrew prefix (keeping ``min_stem_length`` letters)."""
    stem = SEARCH_CONFIG["min_stem_length"]
    return {word, *(word[len(prefix):] for prefix in SEARCH_CONFIG["hebrew_prefixes"]
                    if word.startswith(prefix) and len(word) - len(prefix) >= stem)}


@dataclass(frozen=True)
class _Name:
    text: str
    norm: str
    words: tuple[frozenset[str], ...]

    @classmethod
    def of(cls, text: str) -> _Name:
        norm = normalize_name(text)
        return cls(text, norm, tuple(frozenset(_variants(word)) for word in norm.split()))


def _inside(short: _Name, long: _Name) -> bool:
    """The words of ``short`` appear in order and next to each other among those of ``long``."""
    size = len(short.words)
    if not size or len(short.norm) < SEARCH_CONFIG["min_stem_length"]:
        return False
    return any(all(a & b for a, b in zip(short.words, long.words[start:start + size]))
               for start in range(len(long.words) - size + 1))


@dataclass(frozen=True)
class Match:
    """A plan page or label whose name equals (``exact``) or partly matches an entity's name."""

    kind: str  # "page" | "label"
    item_id: str
    title: str
    entity_name: str
    plan_name: str
    exact: bool


class PlanNames:
    """Every name of the plan's pages and labels, indexed by word for the comparison."""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        self._names: list[tuple[str, str, str, _Name]] = []
        for page_id, page in plan["pages"].items():
            aliases = page.get("aliases") or {}
            for text in (page["title_he"], *aliases.get("he", []), *aliases.get("en", [])):
                self._names.append(("page", page_id, page["title_he"], _Name.of(text)))
        for label_id, label in plan["labels"].items():
            self._names.append(("label", label_id, label["title_he"], _Name.of(label["title_he"])))
        self._by_word: dict[str, set[int]] = {}
        for index, (*_, name) in enumerate(self._names):
            for variants in name.words:
                for variant in variants:
                    self._by_word.setdefault(variant, set()).add(index)

    def matches(self, names: Iterable[str]) -> list[Match]:
        """The plan items one of ``names`` equals or partly matches, exact ones first."""
        found: dict[tuple[str, str], Match] = {}
        for text in dict.fromkeys(names):
            name = _Name.of(text)
            near = {i for variants in name.words for v in variants for i in self._by_word.get(v, ())}
            for index in sorted(near):
                kind, item_id, title, other = self._names[index]
                exact = name.norm == other.norm
                if not (exact or _inside(name, other) or _inside(other, name)):
                    continue
                known = found.get((kind, item_id))
                if known is None or (exact and not known.exact):
                    found[(kind, item_id)] = Match(kind, item_id, title, text, other.text, exact)
        return sorted(found.values(), key=lambda match: not match.exact)


# --------------------------------------------------------------------------------- rule


@dataclass(frozen=True)
class Candidate:
    """An entity named by at least ``min_claims`` claims and what the rule made of it."""

    entity_id: str
    title: str
    names: tuple[str, ...]
    claim_count: int
    pages: tuple[str, ...]  # published pages that cite it, in the plan's order
    matches: tuple[Match, ...]
    decision: str | None
    status: str

    @property
    def tag_id(self) -> str:
        return tag_id(self.entity_id)


@dataclass(frozen=True)
class EntityTags:
    """The rule's result over one set of inputs."""

    claim_counts: dict[str, int]  # every entity named by a claim
    candidates: tuple[Candidate, ...]  # by title

    @property
    def tags(self) -> list[Candidate]:
        return [c for c in self.candidates if c.status == TAGGED]

    @property
    def waiting(self) -> list[Candidate]:
        """Uncertain entities the owner has not decided on yet (no tag until they do)."""
        return [c for c in self.candidates if c.status == UNCERTAIN]

    @property
    def partial(self) -> list[Candidate]:
        """Entities on enough pages whose only matches are partial: decided or not."""
        return [c for c in self.candidates
                if c.status != FEW_PAGES and c.matches and not any(m.exact for m in c.matches)]

    def counts(self) -> dict[str, int]:
        found = Counter(c.status for c in self.candidates)
        return {"candidates": len(self.candidates), **{status: found[status] for status in STATUSES}}

    def table(self, thresholds: Sequence[int] = TAGS_CONFIG["report_min_claims"]) -> list[dict[str, int]]:
        """How many entities are named by at least N claims, for every N."""
        return [{"min_claims": n, "entities": sum(1 for count in self.claim_counts.values() if count >= n)}
                for n in thresholds]


def _status(pages: Sequence[str], matches: Sequence[Match], decision: str | None) -> str:
    if len(pages) < TAGS_CONFIG["min_pages"]:
        return FEW_PAGES
    if any(match.exact for match in matches):
        return COVERED
    if matches:
        return {COVERED: COVERED, TAG: TAGGED}.get(decision, UNCERTAIN)
    return TAGGED


def entity_tags(
    records: Sequence[Record],
    names: Mapping[str, Sequence[str]],
    plan: Mapping[str, Any],
    page_claims: Mapping[str, Collection[str]],
    decisions: Mapping[str, str],
    withheld: Collection[str],
) -> EntityTags:
    """The owner's rule (module docstring) over the claim records (C6), the entities' names
    (``entity_names``), the plan (C7), the claims each published page cites (``cited_claims``
    or ``assigned_claims``; pages not in the plan are left out), the owner's decisions and the
    short ids of the withheld claims (``withheld_ids``)."""
    claim_counts: Counter[str] = Counter(e for record in records for e in record["entity_ids"])
    titles = {e: name for record in records for e, name in zip(record["entity_ids"], record["entity_names"])}
    entities_of = {record["short_id"]: record["entity_ids"] for record in records}
    order = [node["id"] for node, _ in iter_nodes(plan["tree"]) if node["id"] in plan["pages"]]
    pages_of: dict[str, list[str]] = {}
    for page_id in order:
        cited = {e for sid in page_claims.get(page_id, ()) if sid not in withheld for e in entities_of.get(sid, ())}
        for entity_id in cited:
            pages_of.setdefault(entity_id, []).append(page_id)
    plan_names = PlanNames(plan)
    candidates = []
    for entity_id, count in claim_counts.items():
        if count < TAGS_CONFIG["min_claims"]:
            continue
        own = tuple(dict.fromkeys([titles[entity_id], *names.get(entity_id, ())]))
        pages = tuple(pages_of.get(entity_id, ()))
        matches = tuple(plan_names.matches(own))
        decision = decisions.get(entity_id)
        candidates.append(Candidate(entity_id, titles[entity_id], own, count, pages, matches, decision,
                                    _status(pages, matches, decision)))
    candidates.sort(key=lambda c: (c.title, c.entity_id))
    return EntityTags(dict(claim_counts), tuple(candidates))


# ------------------------------------------------------------------------------- report


@dataclass(frozen=True)
class TagInputs:
    """The files the rule reads outside the claim records."""

    sources: ClaimSources = ClaimSources()
    plan: Path | None = None
    pages: Path | None = None
    assignments: Path | None = None
    decisions: Path = ENTITY_TAG_DECISIONS_PATH

    def names(self) -> dict[str, list[str]]:
        path = self.sources.entities or resolve_entities_path()
        return entity_names(read_json_file(path)) if path.is_file() else {}

    def page_claims(self) -> tuple[str, dict[str, set[str]]]:
        """Where the pages come from and what each cites: the written pages when there are
        some, else the assignments' estimate, else nothing."""
        pages = read_json_if_present(self.pages or resolve_wiki_pages_path())
        if pages and pages.get("pages"):
            return FROM_PAGES, cited_claims(pages["pages"])
        assignments = read_json_if_present(self.assignments or resolve(ASSIGNMENTS))
        if assignments:
            return FROM_ASSIGNMENTS, assigned_claims(assignments)
        return NO_PAGES, {}

    def load(self) -> TagData:
        """Everything the rule reads, from the files on disk."""
        records = load_claim_records(self.sources)
        source, page_claims = self.page_claims()
        return TagData(records, self.names(), read_json_file(self.plan or resolve_plan_path()), source,
                       page_claims, load_decisions(self.decisions),
                       withheld_ids(records, self.sources))


@dataclass
class TagData:
    """The rule's inputs in memory; ``source`` says where ``page_claims`` come from. Only the
    owner's ``decisions`` change while the Plan page holds it."""

    records: list[Record]
    names: dict[str, list[str]]
    plan: dict[str, Any]
    source: str
    page_claims: dict[str, set[str]]
    decisions: dict[str, str]
    withheld: frozenset[str]

    def result(self) -> EntityTags:
        return entity_tags(self.records, self.names, self.plan, self.page_claims, self.decisions,
                           self.withheld)


SOURCE_NOTES = {
    FROM_PAGES: "pages = the written pages (step 7) that cite the entity",
    FROM_ASSIGNMENTS: "pages ESTIMATED from the assignments (step 6b): step 7 has not written pages yet",
    NO_PAGES: "no pages and no assignments yet: every entity is on 0 pages",
}


def report_lines(source: str, result: EntityTags) -> Iterator[str]:
    """The report the owner reads (English; numbers only, no entity name)."""
    yield "Entities named by at least N claims:"
    for row in result.table():
        yield f"  N = {row['min_claims']:>4}: {row['entities']} entities"
    counts = result.counts()
    yield (f"With min_claims = {TAGS_CONFIG['min_claims']} and min_pages = {TAGS_CONFIG['min_pages']} "
           f"({SOURCE_NOTES[source]}):")
    yield f"  named by enough claims:          {counts['candidates']}"
    yield f"  on fewer than {TAGS_CONFIG['min_pages']} pages:           {counts[FEW_PAGES]}"
    yield f"  covered by a plan page or label: {counts[COVERED]}"
    yield f"  uncertain, waiting for the owner on the Plan page: {counts[UNCERTAIN]}"
    yield f"  tagged:                          {counts[TAGGED]}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Entity tags: how many entities get a tag page.")
    parser.add_argument("--report", action="store_true", required=True,
                        help="log the counts for the thresholds of config.json (site.entity_tags)")
    parser.parse_args(argv)
    setup_step_logging(STEP_11)
    data = TagInputs().load()
    for line in report_lines(data.source, data.result()):
        logger.info(line)


if __name__ == "__main__":
    main()
