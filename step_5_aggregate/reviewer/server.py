"""FastAPI server for the aggregate cluster review tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel

from step_5_aggregate.reviewer.store import AggregateStore, SortKind, SortOrder
from utils.reviewer_server import StoreRegistry, make_reviewer_app, mount_static as _mount_static

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = make_reviewer_app("Aggregate Cluster Reviewer")
_registry = StoreRegistry(
    AggregateStore,
    {"aggregated_path": None, "claims_path": None, "audit_path": None},
)


def configure_store(
    *,
    aggregated_path: Path | str | None = None,
    claims_path: Path | str | None = None,
    audit_path: Path | str | None = None,
) -> None:
    _registry.configure(
        aggregated_path=aggregated_path,
        claims_path=claims_path,
        audit_path=audit_path,
    )


def get_store() -> AggregateStore:
    return _registry.get()


class RepresentativeRequest(BaseModel):
    source_claim_id: str


class MoveMemberRequest(BaseModel):
    source_claim_id: str
    target_group_key: str


class SplitRequest(BaseModel):
    source_claim_ids: list[str]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return get_store().meta()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return get_store().stats()


@app.get("/api/topics")
def list_topics(
    size_min: int | None = Query(None, ge=1),
    size_max: int | None = Query(None, ge=1),
) -> dict[str, Any]:
    return {
        "items": get_store().list_topics(size_min=size_min, size_max=size_max)
    }


@app.get("/api/topics/{topic_id}/groups")
def list_groups(
    topic_id: str,
    size_min: int | None = Query(None, ge=1),
    size_max: int | None = Query(None, ge=1),
    sort: SortKind = Query("support"),
    order: SortOrder = Query("desc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    try:
        items, total = store.list_groups(
            topic_id,
            size_min=size_min,
            size_max=size_max,
            sort=sort,
            order=order,
            offset=offset,
            limit=limit,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}") from None
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/topics/{topic_id}/groups/{group_key}")
def get_group(
    topic_id: str,
    group_key: str,
    size_min: int | None = Query(None, ge=1),
    size_max: int | None = Query(None, ge=1),
    sort: SortKind = Query("support"),
    order: SortOrder = Query("desc"),
) -> dict[str, Any]:
    store = get_store()
    try:
        return store.get_group(
            topic_id,
            group_key,
            size_min=size_min,
            size_max=size_max,
            sort=sort,
            order=order,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="group not found") from None


@app.post("/api/topics/{topic_id}/groups/{group_key}/representative")
def set_representative(
    topic_id: str, group_key: str, body: RepresentativeRequest
) -> dict[str, Any]:
    store = get_store()
    try:
        group = store.set_representative(topic_id, group_key, body.source_claim_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"group": group, "meta": store.meta()}


@app.post("/api/topics/{topic_id}/groups/{group_key}/move-member")
def move_member(topic_id: str, group_key: str, body: MoveMemberRequest) -> dict[str, Any]:
    store = get_store()
    try:
        group = store.move_member(
            topic_id,
            group_key,
            source_claim_id=body.source_claim_id,
            target_group_key=body.target_group_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"group": group, "meta": store.meta()}


@app.post("/api/topics/{topic_id}/groups/{group_key}/split")
def split_cluster(topic_id: str, group_key: str, body: SplitRequest) -> dict[str, Any]:
    store = get_store()
    try:
        result = store.split_cluster(
            topic_id, group_key, source_claim_ids=body.source_claim_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {**result, "meta": store.meta()}


def mount_static() -> None:
    _mount_static(app, STATIC_DIR)


mount_static()
