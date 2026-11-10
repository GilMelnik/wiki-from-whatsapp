"""The plan review tool of the review server: ``python -m review_tool --tool plan``.

The owner reviews the wiki plan the planning model proposed (contract C7) and makes it
theirs: renames, re-scopes, merges, splits, adds, deletes and moves pages and categories, and
manages labels. Every change is one call of ``step_6_plan.operations``; the plan it returns is
validated, so an invalid plan is never written. Endpoints, under ``/api/plan``:

- ``GET  /plan``                  the plan as the tool shows it (tree rows, pages, labels by
  kind, page budget, branch depths, warnings, coverage, changes since the proposal, undo)
- ``GET  /pages/{page_id}``       the page's example groups with their claim text + coverage
- ``GET  /coverage/unplaced``     sampled groups the coverage check could not place
- ``GET  /ids?id=``               whether an id can be given to a new item (Hebrew reason)
- ``POST /operations/preview``    an operation (``{op, ...}``, see ``requests``) → what it
  would do downstream (``edit``: created, removed_pages, removed_labels, redirects, reassign)
- ``POST /operations``            the same operation, saved → ``{overview, label, edit, focus}``
- ``POST /undo``                  the newest change of this session undone →
  ``{overview, label, focus}``

Unknown page → 404; a change that breaks the plan's rules (or nothing to undo) → 409 with the
rules listed; missing plan → 409; plan file locked by a step → 423.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from step_6_plan.reviewer.requests import Operation
from step_6_plan.reviewer.store import ItemNotFound, PlanConflict, PlanHistory, PlanStore
from utils.paths import PLAN_COVERAGE_PATH

PLAN_KEY = "plan"
GROUPS_KEY = "groups"


class PlanTool:
    NAME = "plan"
    TITLE = "תוכנית הוויקי"
    READS = (PLAN_KEY, GROUPS_KEY)
    WRITES = (PLAN_KEY,)

    def __init__(
        self,
        graph: ArtifactGraph,
        *,
        coverage_path: Path = PLAN_COVERAGE_PATH,
        lock_timeout: float | None = None,
    ) -> None:
        plan, groups = graph.node(PLAN_KEY), graph.node(GROUPS_KEY)
        self._history = PlanHistory()
        factory = partial(
            PlanStore, plan.original, plan.edited, groups.original, groups.edited,
            coverage_path, self._history,
        )
        # The coverage report is written by step 6 next to the plan; watched so a re-plan's
        # new numbers show up.
        self._handle: StoreHandle[PlanStore] = StoreHandle(
            factory,
            (*graph.files(*self.READS), coverage_path),
            lock_paths=graph.files(*self.WRITES),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        for path, endpoint in (
            ("/plan", self.overview),
            ("/pages/{page_id}", self.page),
            ("/coverage/unplaced", self.unplaced),
            ("/ids", self.check_id),
        ):
            router.add_api_route(path, endpoint, methods=["GET"])
        for path, endpoint in (
            ("/operations/preview", self.preview),
            ("/operations", self.apply),
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
    except PlanConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
