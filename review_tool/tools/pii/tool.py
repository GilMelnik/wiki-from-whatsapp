"""The PII review tool of the review server: ``python -m review_tool --tool pii``.

The owner goes through every claim whose phone numbers or emails step 3 scrubbed and either
keeps the removal or restores the values (all, or some). Endpoints, under ``/api/pii``:

- ``GET  /summary``                        counters, pending redactions by type, file read
- ``GET  /claims?offset&limit``            one page of the queue: to decide, removal kept,
  restored (``status`` says which)
- ``GET  /claims/{claim_id}``              the claim, its place in the queue, and what the
  decision rests on: its chat messages (``messages``, each with the details it ``holds``) and
  per removed detail the entities that list it as a contact (``owners``)
- ``POST /claims/{claim_id}/decision``     ``{decision, restore?}`` → ``{claim, next_id,
  summary}`` (``next_id``: the next claim still to decide)
- ``POST /claims/{claim_id}/undo``         → ``{claim, summary}``

Unknown claim → 404; a change that does not fit the claim's state → 409; missing claims file
→ 409; claims file locked by a step → 423. The chat export and the entities are read when
they exist (``review_tool.tools.pii.context``); without them the claim shows no messages or
owners.
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
from review_tool.tools.pii.context import ChatMessages, KnownContacts, with_context
from review_tool.tools.pii.store import ClaimNotFound, Decision, PiiReviewStore, ReviewConflict

CLAIMS_KEY = "claims"
EXPORT_KEY = "export"
ENTITIES_KEY = "entities"


class DecisionRequest(BaseModel):
    decision: Decision
    # Mark numbers (from 0, in text order) to restore; omitted = every mark.
    restore: list[int] | None = None


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
        export = graph.node(EXPORT_KEY).original
        entities = graph.node(ENTITIES_KEY)
        self._chat: StoreHandle[ChatMessages] = StoreHandle(partial(ChatMessages, export), (export,))
        self._contacts: StoreHandle[KnownContacts] = StoreHandle(
            partial(KnownContacts, entities.original, entities.edited), graph.files(ENTITIES_KEY)
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

    def claims(self, limit: int = Query(ge=1), offset: int = Query(0, ge=0)) -> dict[str, Any]:
        return self._handle.get().page(offset, limit)

    def claim(self, claim_id: str) -> dict[str, Any]:
        with _as_http_errors():
            detail = self._handle.get().detail(claim_id)
        claim = with_context(detail["claim"], self._chat.get(), self._contacts.get())
        return {**detail, "claim": claim}

    def decide(self, claim_id: str, body: DecisionRequest) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.decide(claim_id, body.decision, body.restore)

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
