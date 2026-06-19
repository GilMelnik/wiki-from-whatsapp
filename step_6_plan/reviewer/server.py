"""FastAPI server for the wiki plan review tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from step_6_plan.reviewer.store import PlanStore
from utils.reviewer_server import StoreRegistry, make_reviewer_app, mount_static as _mount_static

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = make_reviewer_app("Wiki Plan Reviewer")
_registry = StoreRegistry(PlanStore, {"plan_path": None, "aggregated_path": None})


def configure_store(
    *,
    plan_path: Path | str | None = None,
    aggregated_path: Path | str | None = None,
) -> None:
    _registry.configure(plan_path=plan_path, aggregated_path=aggregated_path)


def get_store() -> PlanStore:
    return _registry.get()


class PageUpdateRequest(BaseModel):
    title: str | None = None
    category: str | None = None
    search_focus: str | None = None
    rationale: str | None = None


class MergePagesRequest(BaseModel):
    source_id: str
    target_id: str


class MoveClaimRequest(BaseModel):
    topic_id: str
    claim_key: str
    target_topic_id: str = Field(..., min_length=1)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return get_store().meta()


@app.get("/api/categories")
def categories() -> dict[str, Any]:
    return {"items": get_store().list_categories()}


@app.get("/api/pages")
def list_pages() -> dict[str, Any]:
    store = get_store()
    return {
        "items": store.list_pages(),
        "sections": store.list_pages_grouped(),
    }


@app.get("/api/pages/{page_id}")
def get_page(page_id: str) -> dict[str, Any]:
    store = get_store()
    page = store.get_page(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return {"page": page}


@app.patch("/api/pages/{page_id}")
def update_page(page_id: str, body: PageUpdateRequest) -> dict[str, Any]:
    store = get_store()
    if store.get_page(page_id) is None:
        raise HTTPException(status_code=404, detail="page not found")
    try:
        page = store.update_page(
            page_id,
            title=body.title,
            category=body.category,
            search_focus=body.search_focus,
            rationale=body.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"page": page, "meta": store.meta()}


@app.post("/api/pages/merge")
def merge_pages(body: MergePagesRequest) -> dict[str, Any]:
    store = get_store()
    if store.get_page(body.source_id) is None:
        raise HTTPException(status_code=404, detail="source page not found")
    if store.get_page(body.target_id) is None:
        raise HTTPException(status_code=404, detail="target page not found")
    try:
        page = store.merge_pages(body.source_id, body.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"page": page, "meta": store.meta(), "sections": store.list_pages_grouped()}


@app.get("/api/pages/{page_id}/claims")
def list_claims(
    page_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    if store.get_page(page_id) is None:
        raise HTTPException(status_code=404, detail="page not found")
    items, total = store.list_claims(page_id, offset=offset, limit=limit)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/topics")
def list_topics() -> dict[str, Any]:
    return {"items": get_store().list_topics()}


@app.post("/api/claims/move")
def move_claim(body: MoveClaimRequest) -> dict[str, Any]:
    store = get_store()
    try:
        claim = store.move_claim(
            topic_id=body.topic_id,
            claim_key=body.claim_key,
            target_topic_id=body.target_topic_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"claim": claim, "meta": store.meta()}


def mount_static() -> None:
    _mount_static(app, STATIC_DIR)
