"""How step 8 cuts the wiki into review passes, and what each pass's fingerprint covers.

A review run is a set of passes; each pass is one model call with its own input, its own cap
of suggestions (``proposals.Scope``) and its own fingerprint.

- PART passes. Walking down the plan tree from its top-level nodes, a node whose written pages
  fit ``wiki_review.part.max_input_tokens`` together is one part; otherwise each of its
  children is looked at by the same rule (a page node with pages below it is then a part of
  its own). A single page is always a part. Small siblings are never packed together. A part's
  id is the id of its top node. "Fit" is counted by characters
  (``wiki_review.chars_per_token``, measured on the Hebrew review prompt): the part prompt's
  rules, the plan block of the whole wiki, then each page's block as it reads alone.
- The CROSS pass (id ``wiki_review.cross.id``) reads the plan block and the stored points of
  every page that has them; a page without points (written before step 7 listed them) is left
  out (``without_points``). With fewer than two such pages there is no cross pass.

Fingerprints: a part's covers the reviewer (model, prompt version, the pass kind and its cap),
its pages (title and effective body, ``inputs.page_fingerprint``) and the plan fields of its
pages and of the nodes above them; the cross pass's covers the reviewer and its whole input
(plan block and every page's title, labels and points). A run asks only the passes whose
fingerprint differs from the one stored in ``metadata.inputs.passes``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from step_10_update.estimate import CharTokenCounter
from step_6_plan.model import iter_nodes
from step_6_plan.run import TokenCounter
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput, page_fingerprint
from step_8_review.prompts import PART_SYSTEM, cross_prompt, page_block, part_prompt
from step_8_review.proposals import KINDS, MISTAKE, Scope
from utils.config import LLM_CONFIG
from utils.llm_client import CacheSegment, _flatten
from utils.llm_client.providers import PROVIDERS
from utils.reconcile import fingerprint

PART, CROSS = "part", "cross"
CROSS_ID = REVIEW_CONFIG["cross"]["id"]
CROSS_KINDS = tuple(kind for kind in KINDS if kind != MISTAKE)


@dataclass(frozen=True)
class ReviewPass:
    """One model call of a review run."""

    id: str
    kind: str                 # PART | CROSS
    pages: tuple[str, ...]
    scope: Scope
    fingerprint: str


def cut(tree: Sequence[Mapping[str, Any]], sizes: Mapping[str, int], limit: int
        ) -> list[tuple[str, tuple[str, ...]]]:
    """``(part id, page ids)`` in tree order: see the module docstring (``sizes``: tokens of
    each written page; ``limit``: what the pages of one part may add up to)."""
    parts: list[tuple[str, tuple[str, ...]]] = []
    for node in tree:
        pages = tuple(n["id"] for n, _ in iter_nodes([node]) if n["id"] in sizes)
        if not pages:
            continue
        if len(pages) == 1 or sum(sizes[pid] for pid in pages) <= limit:
            parts.append((node["id"], pages))
            continue
        if node["id"] in sizes:
            parts.append((node["id"], (node["id"],)))
        parts.extend(cut(node["children"], sizes, limit))
    return parts


def batch_available(stage: str = REVIEW_CONFIG["stage"]) -> bool:
    """Whether the stage's provider has a batch API (read without building a client)."""
    return PROVIDERS[LLM_CONFIG["stages"][stage]["provider"]].supports_batch


def stored_passes(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """The pass fingerprints a suggestions file stores (none in a one-call review's file)."""
    return dict(((metadata or {}).get("inputs") or {}).get("passes") or {})


def raised_about(suggestions: Iterable[Mapping[str, Any]], pages: Collection[str]
                 ) -> list[Mapping[str, Any]]:
    """The suggestions a pass is shown: those about one of its pages (or about no page)."""
    return [s for s in suggestions if not s["pages"] or set(s["pages"]) & set(pages)]


class ReviewPasses:
    """The passes of the current wiki for a reviewer ``model``."""

    def __init__(self, review: ReviewInput, model: str) -> None:
        self.review = review
        self.model = model
        self.chars = CharTokenCounter(REVIEW_CONFIG["chars_per_token"])
        self.without_points = [pid for pid in review.pages
                               if not review.all_pages[pid].get("points")]
        sizes = {pid: self.chars("", page_block(review, pid, (pid,))) for pid in review.pages}
        limit = (REVIEW_CONFIG["part"]["max_input_tokens"]
                 - self.chars(PART_SYSTEM, review.plan_block()))
        self.passes = [self._part(part_id, pages)
                       for part_id, pages in cut(review.plan["tree"], sizes, limit)]
        with_points = [pid for pid in review.pages if pid not in self.without_points]
        if len(with_points) >= 2:
            self.passes.append(self._cross(tuple(with_points)))
        self.by_id = {p.id: p for p in self.passes}

    def _reviewer(self, kind: str, cap: int) -> dict[str, Any]:
        return {"model": self.model, "prompt_version": REVIEW_CONFIG["prompt_version"],
                "kind": kind, "max_suggestions": cap}

    def _plan_fields(self, pages: Sequence[str]) -> dict[str, Any]:
        plan = self.review.plan
        nodes = {node["id"]: node for node, _ in iter_nodes(plan["tree"])}
        fields: dict[str, Any] = {}
        for page_id in pages:
            page = plan["pages"][page_id]
            fields[page_id] = {key: page.get(key) for key in ("title_he", "description",
                                                              "scope", "path")}
            for node_id in page["path"]:
                node = nodes.get(node_id, {})
                fields[node_id] = {"title_he": node.get("title_he"),
                                   "description": node.get("description")}
        return fields

    def _part(self, part_id: str, pages: tuple[str, ...]) -> ReviewPass:
        cap = REVIEW_CONFIG["part"]["max_suggestions"]
        groups = frozenset(gid for pid in pages for gid in self.review.pages[pid].group_ids)
        stamp = fingerprint({
            "reviewer": self._reviewer(PART, cap),
            "pages": {pid: page_fingerprint(self.review.all_pages[pid]) for pid in pages},
            "plan": self._plan_fields(pages)})
        return ReviewPass(part_id, PART, pages, Scope(frozenset(pages), groups, KINDS, cap), stamp)

    def _cross(self, pages: tuple[str, ...]) -> ReviewPass:
        cap = REVIEW_CONFIG["cross"]["max_suggestions"]
        groups = frozenset(gid for pid in pages for point in self.review.all_pages[pid]["points"]
                           for gid in point["groups"])
        _, user = cross_prompt(self.review, pages, [])
        stamp = fingerprint({"reviewer": self._reviewer(CROSS, cap), "input": user[0].text})
        return ReviewPass(CROSS_ID, CROSS, pages,
                          Scope(frozenset(pages), groups, CROSS_KINDS, cap), stamp)

    def select(self, only: Collection[str] | None) -> list[ReviewPass]:
        """The passes named in ``only`` (every pass for ``None``); an unknown id is an error."""
        if only is None:
            return list(self.passes)
        unknown = sorted(set(only) - set(self.by_id))
        if unknown:
            raise ValueError(f"unknown review pass {unknown}; the passes of this wiki are: "
                             f"{', '.join(self.by_id) or 'none'}")
        return [p for p in self.passes if p.id in only]

    def due(self, stored: Mapping[str, str], only: Collection[str] | None, rebuild: bool
            ) -> list[ReviewPass]:
        """The selected passes whose input changed since their last answer (all on a rebuild)."""
        return [p for p in self.select(only) if rebuild or stored.get(p.id) != p.fingerprint]

    def prompt(self, review_pass: ReviewPass, suggestions: Sequence[Mapping[str, Any]]
               ) -> tuple[str, list[CacheSegment]]:
        """``(system, user)`` of one pass, shown the ``suggestions`` about its pages."""
        raised = raised_about(suggestions, review_pass.pages)
        if review_pass.kind == CROSS:
            return cross_prompt(self.review, review_pass.pages, raised)
        return part_prompt(self.review, review_pass.id, review_pass.pages, raised)

    def estimate(self, stored: Mapping[str, str], suggestions: Sequence[Mapping[str, Any]],
                 count_tokens: TokenCounter, *, only: Collection[str] | None = None,
                 rebuild: bool = False, batch: bool) -> dict[str, Any]:
        """Every pass with its pages, input tokens and whether it is due; ``calls`` and
        ``input_tokens`` add up the due ones. No model call."""
        due = {p.id for p in self.due(stored, only, rebuild)}
        rows = []
        for review_pass in self.passes:
            system, user = self.prompt(review_pass, suggestions)
            rows.append({"id": review_pass.id, "pages": list(review_pass.pages),
                         "input_tokens": count_tokens(system, _flatten(user)),
                         "due": review_pass.id in due})
        return {"passes": rows, "calls": len(due), "batch": batch,
                "input_tokens": sum(row["input_tokens"] for row in rows if row["due"])}
