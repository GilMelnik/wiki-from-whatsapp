"""The plan review tool of the review server: ``python -m review_tool --tool plan``.

The owner reviews the wiki plan the planning model proposed (contract C7) and makes it
theirs: renames, re-scopes, merges, splits, adds, deletes and moves pages and categories, and
manages labels. Every change is one call of ``step_6_plan.operations``; the plan it returns is
validated, so an invalid plan is never written. Once step 6b has run, the owner also checks
where the classifier put each claim (contract C8): moves claims between pages and labels
(which pins them) and decides on the step's proposals. The claims are the C6 records
(``step_6_plan.claim_records``), joined from the claims, the entities, step 3's audit and the
manual contradictions; a change to any of them reloads the store. Endpoints, under
``/api/plan``:

- ``GET  /plan``                  the plan as the tool shows it (tree rows, pages, labels by
  kind, page budget, branch depths, warnings, coverage, changes since the proposal, undo)
- ``GET  /pages/{page_id}``       the page's example claims with their text + coverage
- ``GET  /coverage/unplaced``     sampled claims the coverage check could not place
- ``GET  /ids?id=``               whether an id can be given to a new item (English reason)
- ``POST /operations/preview``    an operation (``{op, ...}``, see ``requests``) → what it
  would do downstream (``edit``: created, removed_pages, removed_labels, redirects, reassign)
- ``POST /operations``            the same operation, saved → ``{overview, label, edit, focus}``
- ``GET  /assignments/claims``     a list of claims (``scope`` page | label | unplaced |
  waiting, ``id``, ``role`` all | primary | mention, ``sort`` uncertain | date | support,
  ``q``, ``offset``, ``limit``) → ``{items, total, offset, limit, counts, manual}``
- ``POST /assignments/edit``       ``{claims, changes: [{action, page_id?, label_id?}]}``
  → ``{overview, label, changed}`` (see ``assignments.ClaimChange``)
- ``GET  /proposals``              the step's proposals, pending first, with their claims
- ``POST /proposals/{id}/accept``  ``{operation}`` (the plan operation, as corrected) → saved
  with ``status: accepted`` in one change → ``{overview, label, edit, focus}``
- ``POST /proposals/{id}/reject``, ``/proposals/{id}/reopen`` → ``{overview, label}``
- ``POST /undo``                  the newest change of this session undone (plan and / or
  assignments) → ``{overview, label, focus}``

Unknown page / label / proposal → 404; a change that breaks the plan's rules, a move or a
decision that does not fit (or nothing to undo) → 409 in English; missing plan, or no
assignments file for the assignment endpoints → 409; a file locked by a step → 423. The
assignments are an optional file (step 6b runs after the plan review), so they are a write of
the tool, watched by hand, not a read: without them the plan tool works as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from review_tool.tools.plan.assignment_views import REVIEW_CONFIG, ROLES, SCOPES, SORTS
from review_tool.tools.plan.assignments import AssignmentsConflict, ClaimEdit
from review_tool.tools.plan.history import PlanHistory
from review_tool.tools.plan.proposals import PENDING, REJECTED
from review_tool.tools.plan.store import ItemNotFound, PlanConflict, PlanStore
from step_6_plan.claim_records import ClaimSources
from step_6_plan.operation_requests import Operation
from utils.paths import AUDIT_PATH, MANUAL_CONTRADICTIONS_PATH, PLAN_COVERAGE_PATH

PLAN_KEY = "plan"
CLAIMS_KEY = "claims"
ENTITIES_KEY = "entities"
ASSIGNMENTS_KEY = "assignments"
Scope = Literal[SCOPES]
Role = Literal[ROLES]
Sort = Literal[SORTS]


class PlanTool:
    NAME = "plan"
    TITLE = "Wiki plan"
    READS = (PLAN_KEY, CLAIMS_KEY, ENTITIES_KEY)
    WRITES = (PLAN_KEY, ASSIGNMENTS_KEY)

    def __init__(
        self,
        graph: ArtifactGraph,
        *,
        coverage_path: Path = PLAN_COVERAGE_PATH,
        audit_path: Path = AUDIT_PATH,
        contradictions_path: Path = MANUAL_CONTRADICTIONS_PATH,
        lock_timeout: float | None = None,
    ) -> None:
        plan, assignments = graph.node(PLAN_KEY), graph.node(ASSIGNMENTS_KEY)
        claims, entities = graph.node(CLAIMS_KEY), graph.node(ENTITIES_KEY)
        self._history = PlanHistory()

        def factory() -> PlanStore:
            sources = ClaimSources(claims.effective, entities.effective, audit_path,
                                   contradictions_path)
            return PlanStore(plan.original, plan.edited, sources, assignments.original,
                             assignments.edited, coverage_path, self._history)

        # The coverage report is written by step 6 next to the plan; watched so a re-plan's
        # new numbers show up. The assignments (a write, optional) are watched too, and so are
        # the claim records' inputs that have no node of their own (audit, manual pairs).
        self._handle: StoreHandle[PlanStore] = StoreHandle(
            factory,
            (*graph.files(*self.READS, ASSIGNMENTS_KEY), coverage_path, audit_path,
             contradictions_path),
            lock_paths=graph.files(*self.WRITES),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        for path, endpoint in (
            ("/plan", self.overview),
            ("/pages/{page_id}", self.page),
            ("/coverage/unplaced", self.unplaced),
            ("/ids", self.check_id),
            ("/assignments/claims", self.claims),
            ("/proposals", self.proposals),
        ):
            router.add_api_route(path, endpoint, methods=["GET"])
        for path, endpoint in (
            ("/operations/preview", self.preview),
            ("/operations", self.apply),
            ("/assignments/edit", self.edit_claims),
            ("/proposals/{proposal_id}/accept", self.accept),
            ("/proposals/{proposal_id}/reject", self.reject),
            ("/proposals/{proposal_id}/reopen", self.reopen),
            ("/undo", self.undo),
        ):
            router.add_api_route(path, endpoint, methods=["POST"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def overview(self) -> dict[str, Any]:
        return self._handle.get().overview()

    def page(self, page_id: str) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().page_detail(page_id)

    def unplaced(self) -> dict[str, Any]:
        return self._handle.get().unplaced()

    def check_id(self, id: str = "") -> dict[str, Any]:
        return self._handle.get().check_id(id.strip())

    def preview(self, op: Operation = Body()) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().preview(op)

    def apply(self, op: Operation = Body()) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.apply(op)

    def claims(
        self,
        scope: Scope,
        id: str | None = None,
        role: Role = "all",
        sort: Sort = "uncertain",
        q: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(REVIEW_CONFIG["claims_page_size"], ge=1,
                           le=REVIEW_CONFIG["max_claims_page_size"]),
    ) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().claims_list(scope=scope, item_id=id, role=role, sort=sort,
                                                  q=q, offset=offset, limit=limit)

    def proposals(self) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().proposals()

    def edit_claims(self, edit: ClaimEdit = Body()) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.edit_claims(edit)

    def accept(self, proposal_id: str, operation: Operation = Body(embed=True)) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.accept(proposal_id, operation)

    def reject(self, proposal_id: str) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.decide(proposal_id, REJECTED)

    def reopen(self, proposal_id: str) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.decide(proposal_id, PENDING)

    def undo(self) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.undo()


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except ItemNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except (PlanConflict, AssignmentsConflict) as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
