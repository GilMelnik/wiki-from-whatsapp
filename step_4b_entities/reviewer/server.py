"""FastAPI server for the entity resolution review tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel

from step_4b_entities.reviewer.store import EntityStore, SortKind, SortOrder, Status
from utils.reviewer_server import (
    StoreRegistry,
    make_reviewer_app,
    mount_static as _mount_static,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = make_reviewer_app("Entity Resolution Reviewer")
_registry = StoreRegistry(
    EntityStore,
    {"entities_path": None, "claims_path": None},
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


@app.post("/api/entities/{entity_id}/status")
def set_status(entity_id: str, body: StatusRequest) -> dict[str, Any]:
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


def mount_static() -> None:
    _mount_static(app, STATIC_DIR)


mount_static()
