"""FastAPI server for the wiki plan review tool."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from plan_reviewer.store import PlanStore

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Wiki Plan Reviewer", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: PlanStore | None = None
_store_config: dict[str, Any] = {"plan_path": None, "aggregated_path": None}
_store_lock = threading.Lock()


def configure_store(
    *,
    plan_path: Path | str | None = None,
    aggregated_path: Path | str | None = None,
) -> None:
    global _store, _store_config
    with _store_lock:
        _store = None
        _store_config = {
            "plan_path": plan_path,
            "aggregated_path": aggregated_path,
        }


def get_store() -> PlanStore:
    global _store
    if _store is not None and _store.loaded:
        return _store
    with _store_lock:
        if _store is None or not _store.loaded:
            _store = PlanStore(**_store_config)
            _store.load()
        return _store


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
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
