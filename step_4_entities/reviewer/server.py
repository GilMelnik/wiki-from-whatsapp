"""FastAPI server for the entity resolution review tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel

from step_4_entities.constants import DEFAULT_ENTITY_ANALYSIS_PATH
from step_4_entities.mentions import DictaAnalyzer
from step_4_entities.reviewer.store import EntityStore, SortKind, SortOrder, Status
from utils.reviewer_server import (
    StoreRegistry,
    make_reviewer_app,
    mount_static as _mount_static,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = make_reviewer_app("Entity Resolution Reviewer")
_registry = StoreRegistry(
    EntityStore,
    {
        "entities_path": None,
        "claims_path": None,
        # Reuse the pipeline's dictabert analysis cache so highlighting/attribution
        # match exactly; the model only loads on a cache miss (e.g. edited claims).
        "analyzer": DictaAnalyzer(),
        "analysis_cache_path": DEFAULT_ENTITY_ANALYSIS_PATH,
    },
)


def configure_store(
    *,
    entities_path: Path | str | None = None,
    claims_path: Path | str | None = None,
) -> None:
    _registry.configure(entities_path=entities_path, claims_path=claims_path)


def get_store() -> EntityStore:
    return _registry.get()


class StatusRequest(BaseModel):
    status: Status


class CanonicalRequest(BaseModel):
    name: str


class RenameRequest(BaseModel):
    canonical_name: str


class ContactsRequest(BaseModel):
    email: list[str] = []
    phone: list[str] = []
    website: list[str] = []


class UncertainContactRequest(BaseModel):
    kind: str
    value: str
    action: str
    new_value: str | None = None


class MoveMemberRequest(BaseModel):
    name: str
    target_entity_id: str | None = None


class MoveClaimsRequest(BaseModel):
    name: str
    claim_ids: list[str]
    target_entity_id: str | None = None


class CopyClaimsRequest(BaseModel):
    name: str
    claim_ids: list[str]
    target_entity_id: str | None = None


class ExcludeClaimsRequest(BaseModel):
    name: str
    claim_ids: list[str]


class MergeRequest(BaseModel):
    target_entity_id: str


class CreateAggregationRequest(BaseModel):
    claim_ids: list[str]
    representative: str


class RepresentativeRequest(BaseModel):
    claim_id: str


class DecoupleRequest(BaseModel):
    claim_id: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return get_store().meta()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return get_store().stats()


@app.get("/api/entities")
def list_entities(
    status: str | None = Query(None),
    size_min: int | None = Query(None, ge=1),
    q: str | None = Query(None),
    sort: SortKind = Query("count"),
    order: SortOrder = Query("desc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    items, total = get_store().list_entities(
        status=status,
        size_min=size_min,
        query=q,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/entities/{entity_id}")
def get_entity(
    entity_id: str,
    status: str | None = Query(None),
    size_min: int | None = Query(None, ge=1),
    q: str | None = Query(None),
    sort: SortKind = Query("count"),
    order: SortOrder = Query("desc"),
) -> dict[str, Any]:
    try:
        return get_store().get_entity(
            entity_id, status=status, size_min=size_min, query=q, sort=sort, order=order
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="entity not found") from None


def _store_call(fn, *args, **kwargs) -> dict[str, Any]:
    store = get_store()
    try:
        result = fn(store, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"entity": result, "meta": store.meta()}


@app.get("/api/entities/{entity_id}/member-claims")
def member_claims(
    entity_id: str,
    name: str = Query(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=200),
) -> dict[str, Any]:
    store = get_store()
    try:
        return store.member_claims(entity_id, name, offset=offset, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/entities/{entity_id}/status")
def set_status(entity_id: str, body: StatusRequest) -> dict[str, Any]:
    # Reject is no longer a label: it un-merges the cluster and trims claims.
    if body.status == "rejected":
        return _store_call(EntityStore.reject, entity_id)
    return _store_call(EntityStore.set_status, entity_id, body.status)


@app.post("/api/entities/{entity_id}/canonical")
def set_canonical(entity_id: str, body: CanonicalRequest) -> dict[str, Any]:
    return _store_call(EntityStore.set_canonical, entity_id, body.name)


@app.post("/api/entities/{entity_id}/rename")
def rename_entity(entity_id: str, body: RenameRequest) -> dict[str, Any]:
    return _store_call(EntityStore.rename_entity, entity_id, body.canonical_name)


@app.post("/api/entities/{entity_id}/contacts")
def set_contacts(entity_id: str, body: ContactsRequest) -> dict[str, Any]:
    return _store_call(
        EntityStore.set_contacts,
        entity_id,
        {"email": body.email, "phone": body.phone, "website": body.website},
    )


@app.post("/api/entities/{entity_id}/uncertain-contact")
def resolve_uncertain_contact(
    entity_id: str, body: UncertainContactRequest
) -> dict[str, Any]:
    return _store_call(
        EntityStore.resolve_uncertain_contact,
        entity_id,
        kind=body.kind,
        value=body.value,
        action=body.action,
        new_value=body.new_value,
    )


@app.delete("/api/entities/{entity_id}")
def delete_entity(entity_id: str) -> dict[str, Any]:
    store = get_store()
    try:
        result = store.delete_entity(entity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {**result, "meta": store.meta()}


@app.post("/api/entities/{entity_id}/move-member")
def move_member(entity_id: str, body: MoveMemberRequest) -> dict[str, Any]:
    return _store_call(
        EntityStore.move_member,
        entity_id,
        body.name,
        target_entity_id=body.target_entity_id,
    )


@app.post("/api/entities/{entity_id}/move-claims")
def move_claims(entity_id: str, body: MoveClaimsRequest) -> dict[str, Any]:
    return _store_call(
        EntityStore.move_claims,
        entity_id,
        body.name,
        body.claim_ids,
        target_entity_id=body.target_entity_id,
    )


@app.post("/api/entities/{entity_id}/copy-claims")
def copy_claims(entity_id: str, body: CopyClaimsRequest) -> dict[str, Any]:
    return _store_call(
        EntityStore.copy_claims,
        entity_id,
        body.name,
        body.claim_ids,
        target_entity_id=body.target_entity_id,
    )


@app.post("/api/entities/{entity_id}/exclude-claims")
def exclude_claims(entity_id: str, body: ExcludeClaimsRequest) -> dict[str, Any]:
    return _store_call(
        EntityStore.exclude_claims,
        entity_id,
        body.name,
        body.claim_ids,
    )


@app.post("/api/entities/{entity_id}/merge")
def merge(entity_id: str, body: MergeRequest) -> dict[str, Any]:
    return _store_call(EntityStore.merge, entity_id, body.target_entity_id)


@app.delete("/api/claims/{claim_id}")
def delete_claim(claim_id: str) -> dict[str, Any]:
    store = get_store()
    try:
        result = store.delete_claim(claim_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {**result, "meta": store.meta()}


def _aggregation_call(fn, *args, **kwargs) -> dict[str, Any]:
    store = get_store()
    try:
        result = fn(store, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"aggregation": result, "meta": store.meta()}


@app.get("/api/aggregations")
def list_aggregations() -> dict[str, Any]:
    return {"aggregations": get_store().aggregations()}


@app.post("/api/aggregations")
def create_aggregation(body: CreateAggregationRequest) -> dict[str, Any]:
    return _aggregation_call(
        EntityStore.create_aggregation, body.claim_ids, body.representative
    )


@app.post("/api/aggregations/{group_id}/representative")
def set_representative(group_id: str, body: RepresentativeRequest) -> dict[str, Any]:
    return _aggregation_call(EntityStore.set_representative, group_id, body.claim_id)


@app.post("/api/aggregations/{group_id}/decouple")
def decouple_claim(group_id: str, body: DecoupleRequest) -> dict[str, Any]:
    return _aggregation_call(EntityStore.decouple_claim, group_id, body.claim_id)


@app.delete("/api/aggregations/{group_id}")
def delete_aggregation(group_id: str) -> dict[str, Any]:
    return _aggregation_call(EntityStore.delete_aggregation, group_id)


def mount_static() -> None:
    _mount_static(app, STATIC_DIR)


mount_static()
