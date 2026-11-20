"""The Plan page's "Entity tags" view: the numbers of the owner's tag rule and a yes / no on each
entity whose name only partly matches a page or label of the plan (``step_11_site.entity_tags``).

Endpoints, under ``/api/plan``:

- ``GET  /entity-tags``               the numbers and the entities with a partial match:
  ``{source, thresholds, table, counts, entities: [{entity_id, name, names, claim_count, pages,
  matches, decision}], problem}`` (``problem``: why there is nothing to show, in English)
- ``POST /entity-tags/{entity_id}``   ``{decision: "covered" | "tag" | null}`` saved (``null``
  forgets it) → the same view

The rule reads the claim records (C6), the entities' names, the effective plan, the written
pages (or, before step 8 has run, the assignments' estimate) and the decisions file; a change to
any of them reloads it. Only the decisions file is written (atomically, under its lock). An entity
that has no partial match now → 409.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from review_tool.artifacts import ArtifactGraph
from review_tool.stores import StoreHandle
from review_tool.tools.plan.store import PlanConflict
from step_5_same_complaint.claim_records import ClaimSources
from step_11_site.entity_tags import TAGS_CONFIG, Candidate, TagData, TagInputs, write_decisions

Decision = Literal["covered", "tag"] | None


class TagDecision(BaseModel):
    """The body of ``POST /entity-tags/{entity_id}``: ``null`` forgets the decision."""

    decision: Decision
READS = ("plan", "claims", "entities", "pages", "assignments")


class EntityTagStore:
    """The rule's inputs in memory, or the reason they could not be read."""

    def __init__(self, inputs: TagInputs) -> None:
        self.inputs = inputs
        self.data: TagData | None = None
        self.problem: str | None = None
        try:
            self.data = inputs.load()
        except (FileNotFoundError, ValueError, KeyError) as exc:
            self.problem = f"The entity tags cannot be computed yet: {exc}"

    def view(self) -> dict[str, Any]:
        thresholds = {key: TAGS_CONFIG[key] for key in ("min_claims", "min_pages")}
        if self.data is None:
            return {"problem": self.problem, "thresholds": thresholds}
        result = self.data.result()
        pages = self.data.plan["pages"]
        return {
            "problem": None,
            "source": self.data.source,
            "thresholds": thresholds,
            "table": result.table(),
            "counts": result.counts(),
            "entities": [_entity(c, lambda page_id: pages[page_id]["title_he"]) for c in result.partial],
        }

    def decide(self, entity_id: str, decision: Decision) -> dict[str, Any]:
        if self.data is None:
            raise PlanConflict(self.problem)
        if entity_id not in {c.entity_id for c in self.data.result().partial}:
            raise PlanConflict(
                f"Entity {entity_id} does not wait for a decision: its name does not partly match a "
                f"page or label of the plan, or it is on fewer than {TAGS_CONFIG['min_pages']} pages.")
        decisions = dict(self.data.decisions)
        if decision is None:
            decisions.pop(entity_id, None)
        else:
            decisions[entity_id] = decision
        write_decisions(decisions, self.inputs.decisions)
        self.data.decisions = decisions
        return self.view()


def _entity(candidate: Candidate, title: Callable[[str], str]) -> dict[str, Any]:
    return {
        "entity_id": candidate.entity_id,
        "name": candidate.title,
        "names": list(candidate.names),
        "claim_count": candidate.claim_count,
        "pages": [{"id": page_id, "title": title(page_id)} for page_id in candidate.pages],
        "matches": [{"kind": m.kind, "id": m.item_id, "title": m.title, "entity_name": m.entity_name,
                     "plan_name": m.plan_name} for m in candidate.matches],
        "decision": candidate.decision,
    }


class EntityTagsView:
    """The two endpoints' work over one store handle (``tool.PlanTool`` routes them; a refused
    decision raises ``PlanConflict``)."""

    def __init__(self, graph: ArtifactGraph, *, audit_path: Path, contradictions_path: Path,
                 decisions_path: Path, lock_timeout: float | None = None) -> None:
        plan, pages, assignments = (graph.node(key) for key in ("plan", "pages", "assignments"))
        claims, entities = graph.node("claims"), graph.node("entities")

        def factory() -> EntityTagStore:
            sources = ClaimSources(claims.effective, entities.effective, audit_path, contradictions_path)
            return EntityTagStore(TagInputs(sources, plan.effective, pages.effective, assignments.effective,
                                            decisions_path))

        self._handle: StoreHandle[EntityTagStore] = StoreHandle(
            factory,
            (*graph.files(*READS), audit_path, contradictions_path, decisions_path),
            lock_paths=(decisions_path,),
            lock_timeout=lock_timeout,
        )

    def view(self) -> dict[str, Any]:
        return self._handle.get().view()

    def decide(self, entity_id: str, decision: Decision) -> dict[str, Any]:
        with self._handle.mutate() as store:
            return store.decide(entity_id, decision)
