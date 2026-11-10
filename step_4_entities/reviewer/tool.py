"""The entity review tool of the review server: ``python -m review_tool --tool entities``.

The owner checks how step 4 grouped the spellings of agencies, clinics, countries and
services: merges, splits, renames, accepts or rejects them, fixes contacts, excludes or moves
claims, deletes claims and groups near-duplicate claims for step 5. Endpoints, under
``/api/entities`` (view = ``status, flag, q, sort, order``; see ``store.helpers``):

- ``GET  /summary``                              counters, the file read, analysis coverage
- ``GET  /entities?<view>&offset&limit``         one page of the list
- ``GET  /entities/{id}?<view>``                 detail (members + first claims) and queue
- ``GET  /entities/{id}/claims?name&offset&limit``  another page of one member's claims
- ``GET  /deleted-names``                        names of deleted entities
- ``POST /entities/{id}/status`` ``{status, view}`` (accept moves on in the view)
- ``POST /entities/{id}/reject`` (un-group) · ``/rename`` · ``/contacts`` · ``/contact``
  · ``/merge`` · ``/members/move`` · ``/members/confirm`` · ``/claims/move`` · ``/claims/copy``
  · ``/claims/exclude`` · ``/claims/include``
- ``DELETE /entities/{id}?<view>`` · ``DELETE /claims/{claim_id}``
- ``POST /deleted-names/restore`` ``{name}``
- ``POST /aggregations`` · ``POST /aggregations/{id}/representative`` ·
  ``POST /aggregations/{id}/decouple`` · ``DELETE /aggregations/{id}``

Every change answers ``{entity_id, summary}`` (the entity to show next) or, for claim
aggregations, ``{aggregation, summary}``. Unknown entity / member / claim → 404; a change that
does not fit the current state → 409; a missing file → 409; a file locked by a step → 423.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from step_4_entities.reviewer.store import EntityNotFound, EntityStore, EntityView, ReviewConflict
from step_4_entities.reviewer.store.helpers import (
    ContactAction,
    ContactKind,
    FlagFilter,
    SortKind,
    SortOrder,
    Status,
    StatusFilter,
)
from utils.paths import ENTITY_ANALYSIS_PATH

CLAIMS_KEY = "claims"
ENTITIES_KEY = "entities"
AGGREGATIONS_KEY = "claim_aggregations"


class ViewBody(BaseModel):
    status: StatusFilter = "all"
    flag: FlagFilter = "all"
    q: str = ""
    sort: SortKind = "count"
    order: SortOrder = "desc"

    def to_view(self) -> EntityView:
        return EntityView(self.status, self.flag, self.q, self.sort, self.order)


def view_query(
    status: StatusFilter = "all",
    flag: FlagFilter = "all",
    q: str = "",
    sort: SortKind = "count",
    order: SortOrder = "desc",
) -> EntityView:
    return EntityView(status, flag, q, sort, order)


class StatusRequest(BaseModel):
    status: Status
    view: ViewBody = ViewBody()


class RenameRequest(BaseModel):
    canonical_name: str


class ContactsRequest(BaseModel):
    email: list[str] = []
    phone: list[str] = []
    website: list[str] = []


class ContactRequest(BaseModel):
    kind: ContactKind
    value: str
    action: ContactAction
    new_value: str | None = None


class TargetRequest(BaseModel):
    target_entity_id: str


class MemberRequest(BaseModel):
    name: str


class MoveMemberRequest(BaseModel):
    name: str
    target_entity_id: str | None = None  # None = a new entity


class ClaimsRequest(BaseModel):
    name: str
    claim_ids: list[str]


class MoveClaimsRequest(ClaimsRequest):
    target_entity_id: str | None = None  # None = a new entity


class AggregationRequest(BaseModel):
    claim_ids: list[str]
    representative: str


class ClaimRequest(BaseModel):
    claim_id: str


class EntitiesTool:
    NAME = "entities"
    TITLE = "איחוד ישויות"
    READS = (CLAIMS_KEY, ENTITIES_KEY)
    # The claim aggregations file is the tool's own (created by the first claim group), so it
    # is not a read the frame reports as missing; the store still loads and follows it.
    WRITES = (ENTITIES_KEY, AGGREGATIONS_KEY)

    def __init__(
        self,
        graph: ArtifactGraph,
        *,
        analysis_path: Path = ENTITY_ANALYSIS_PATH,
        lock_timeout: float | None = None,
    ) -> None:
        entities, claims = graph.node(ENTITIES_KEY), graph.node(CLAIMS_KEY)
        factory = partial(
            EntityStore,
            entities.original,
            entities.edited,
            claims.original,
            claims.edited,
            graph.node(AGGREGATIONS_KEY).original,
            analysis_path,
        )
        # The analysis cache is watched too: a step-4 run that re-analyzed claims is picked up.
        self._handle: StoreHandle[EntityStore] = StoreHandle(
            factory,
            (*graph.files(*self.READS, AGGREGATIONS_KEY), analysis_path),
            lock_paths=graph.files(*self.WRITES),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        for path, endpoint in (
            ("/summary", self.summary),
            ("/entities", self.entities),
            ("/entities/{entity_id}", self.entity),
            ("/entities/{entity_id}/claims", self.member_claims),
            ("/deleted-names", self.deleted_names),
        ):
            router.add_api_route(path, endpoint, methods=["GET"])
        for path, endpoint in (
            ("/entities/{entity_id}/status", self.set_status),
            ("/entities/{entity_id}/reject", self.reject),
            ("/entities/{entity_id}/rename", self.rename),
            ("/entities/{entity_id}/contacts", self.set_contacts),
            ("/entities/{entity_id}/contact", self.resolve_contact),
            ("/entities/{entity_id}/merge", self.merge),
            ("/entities/{entity_id}/members/move", self.move_member),
            ("/entities/{entity_id}/members/confirm", self.confirm_member),
            ("/entities/{entity_id}/claims/move", self.move_claims),
            ("/entities/{entity_id}/claims/copy", self.copy_claims),
            ("/entities/{entity_id}/claims/exclude", self.exclude_claims),
            ("/entities/{entity_id}/claims/include", self.include_claims),
            ("/deleted-names/restore", self.restore_name),
            ("/aggregations", self.create_aggregation),
            ("/aggregations/{group_id}/representative", self.set_representative),
            ("/aggregations/{group_id}/decouple", self.decouple_claim),
        ):
            router.add_api_route(path, endpoint, methods=["POST"])
        router.add_api_route("/entities/{entity_id}", self.delete_entity, methods=["DELETE"])
        router.add_api_route("/claims/{claim_id}", self.delete_claim, methods=["DELETE"])
        router.add_api_route(
            "/aggregations/{group_id}", self.delete_aggregation, methods=["DELETE"]
        )
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    # --- reads ----------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return self._handle.get().summary()

    def entities(
        self,
        limit: int = Query(ge=1),
        offset: int = Query(0, ge=0),
        view: EntityView = Depends(view_query),
    ) -> dict[str, Any]:
        return self._handle.get().page(view, offset, limit)

    def entity(self, entity_id: str, view: EntityView = Depends(view_query)) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().detail(entity_id, view)

    def member_claims(
        self,
        entity_id: str,
        name: str,
        offset: int = Query(0, ge=0),
        limit: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().member_claims(entity_id, name, offset, limit)

    def deleted_names(self) -> dict[str, Any]:
        return self._handle.get().deleted_names_view()

    # --- changes ----------------------------------------------------------------

    @contextmanager
    def _change(self) -> Iterator[EntityStore]:
        with _as_http_errors(), self._handle.mutate() as store:
            yield store

    def _answer(self, store: EntityStore, entity_id: str | None) -> dict[str, Any]:
        return {"entity_id": entity_id, "summary": store.summary()}

    def set_status(self, entity_id: str, body: StatusRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(
                store, store.set_status(entity_id, body.status, body.view.to_view())
            )

    def reject(self, entity_id: str) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.reject(entity_id))

    def rename(self, entity_id: str, body: RenameRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.rename(entity_id, body.canonical_name))

    def set_contacts(self, entity_id: str, body: ContactsRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.set_contacts(entity_id, body.model_dump()))

    def resolve_contact(self, entity_id: str, body: ContactRequest) -> dict[str, Any]:
        with self._change() as store:
            shown = store.resolve_contact(
                entity_id, body.kind, body.value, body.action, body.new_value
            )
            return self._answer(store, shown)

    def merge(self, entity_id: str, body: TargetRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.merge(entity_id, body.target_entity_id))

    def move_member(self, entity_id: str, body: MoveMemberRequest) -> dict[str, Any]:
        with self._change() as store:
            shown = store.move_member(entity_id, body.name, body.target_entity_id)
            return self._answer(store, shown)

    def confirm_member(self, entity_id: str, body: MemberRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.confirm_member(entity_id, body.name))

    def move_claims(self, entity_id: str, body: MoveClaimsRequest) -> dict[str, Any]:
        with self._change() as store:
            shown = store.move_claims(entity_id, body.name, body.claim_ids, body.target_entity_id)
            return self._answer(store, shown)

    def copy_claims(self, entity_id: str, body: MoveClaimsRequest) -> dict[str, Any]:
        with self._change() as store:
            shown = store.copy_claims(entity_id, body.name, body.claim_ids, body.target_entity_id)
            return self._answer(store, shown)

    def exclude_claims(self, entity_id: str, body: ClaimsRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.exclude_claims(entity_id, body.name, body.claim_ids))

    def include_claims(self, entity_id: str, body: ClaimsRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.include_claims(entity_id, body.name, body.claim_ids))

    def delete_entity(
        self, entity_id: str, view: EntityView = Depends(view_query)
    ) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.delete_entity(entity_id, view))

    def delete_claim(self, claim_id: str) -> dict[str, Any]:
        with self._change() as store:
            store.delete_claim(claim_id)
            return self._answer(store, None)

    def restore_name(self, body: MemberRequest) -> dict[str, Any]:
        with self._change() as store:
            return self._answer(store, store.restore_name(body.name))

    def create_aggregation(self, body: AggregationRequest) -> dict[str, Any]:
        with self._change() as store:
            group = store.aggregations.create(
                body.claim_ids, body.representative, store.claim_usable
            )
            return {"aggregation": group, "summary": store.summary()}

    def set_representative(self, group_id: str, body: ClaimRequest) -> dict[str, Any]:
        with self._change() as store:
            group = store.aggregations.set_representative(group_id, body.claim_id)
            return {"aggregation": group, "summary": store.summary()}

    def decouple_claim(self, group_id: str, body: ClaimRequest) -> dict[str, Any]:
        with self._change() as store:
            group = store.aggregations.decouple(group_id, body.claim_id)
            return {"aggregation": group, "summary": store.summary()}

    def delete_aggregation(self, group_id: str) -> dict[str, Any]:
        with self._change() as store:
            store.aggregations.delete(group_id)
            return {"aggregation": None, "summary": store.summary()}


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except EntityNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
