"""The PII review tool of the review server: ``python -m review_tool --tool pii``.

The owner goes through every claim whose phone numbers or emails step 3 scrubbed and either
accepts the removal or restores the values (all, or some). Endpoints, under ``/api/pii``:

- ``GET  /summary``                        counters, pending redactions by type, file read
- ``GET  /claims?filter&sort&order&offset&limit``  one page of the queue
- ``GET  /claims/{claim_id}?filter&sort&order``    the claim and its place in the queue
- ``POST /claims/{claim_id}/decision``     ``{decision, restore?, filter, sort, order}`` →
  ``{claim, next_id, summary}``
- ``POST /claims/{claim_id}/undo``         → ``{claim, summary}``

``filter`` is pending | reviewed | all; ``sort`` is claim_id | thread_id | date | redactions |
supporters; ``order`` is asc | desc. Unknown claim → 404; a change that does not fit the
claim's state → 409; missing claims file → 409; claims file locked by a step → 423.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from step_3_extract.reviewer.store import (
    ClaimNotFound,
    Decision,
    FilterKind,
    PiiReviewStore,
    QueueView,
    ReviewConflict,
    SortKind,
    SortOrder,
)

CLAIMS_KEY = "claims"


class DecisionRequest(BaseModel):
    decision: Decision
    # Mark numbers (from 0, in text order) to restore; omitted = every mark.
    restore: list[int] | None = None
    # The queue the owner is working through, to pick the next claim in it.
    filter: FilterKind = "pending"
    sort: SortKind = "claim_id"
    order: SortOrder = "asc"


class PiiTool:
    NAME = "pii"
    TITLE = "Privacy"
    READS = (CLAIMS_KEY,)
    WRITES = (CLAIMS_KEY,)

    def __init__(self, graph: ArtifactGraph, *, lock_timeout: float | None = None) -> None:
        node = graph.node(CLAIMS_KEY)
        self._handle: StoreHandle[PiiReviewStore] = StoreHandle(
            partial(PiiReviewStore, node.original, node.edited),
            graph.files(*self.READS),
            lock_paths=graph.files(*self.WRITES),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        router.add_api_route("/summary", self.summary, methods=["GET"])
        router.add_api_route("/claims", self.claims, methods=["GET"])
        router.add_api_route("/claims/{claim_id}", self.claim, methods=["GET"])
        router.add_api_route("/claims/{claim_id}/decision", self.decide, methods=["POST"])
        router.add_api_route("/claims/{claim_id}/undo", self.undo, methods=["POST"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def summary(self) -> dict[str, Any]:
        return self._handle.get().summary()

    def claims(
        self,
        limit: int = Query(ge=1),
        offset: int = Query(0, ge=0),
        filter: FilterKind = "pending",
        sort: SortKind = "claim_id",
        order: SortOrder = "asc",
    ) -> dict[str, Any]:
        return self._handle.get().page(QueueView(filter, sort, order), offset, limit)

    def claim(
        self,
        claim_id: str,
        filter: FilterKind = "pending",
        sort: SortKind = "claim_id",
        order: SortOrder = "asc",
    ) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().detail(claim_id, QueueView(filter, sort, order))

    def decide(self, claim_id: str, body: DecisionRequest) -> dict[str, Any]:
        view = QueueView(body.filter, body.sort, body.order)
        with _as_http_errors(), self._handle.mutate() as store:
            return store.decide(claim_id, body.decision, view, body.restore)

    def undo(self, claim_id: str) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.undo(claim_id)


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except ClaimNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
