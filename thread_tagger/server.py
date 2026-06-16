"""FastAPI server for the thread tagging web tool."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from thread_tagger.models import FilterKind, SortKind, SortOrder
from thread_tagger.stats import compute_stats, enrich_thread
from thread_tagger.store import ThreadStore
from wiki_build.taxonomy import CATEGORIES, TAXONOMY

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Thread Tagger", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: ThreadStore | None = None
_store_config: dict[str, Any] = {"inspect_only": False, "threads_path": None}
_store_lock = threading.Lock()


def configure_store(
    *,
    inspect_only: bool = False,
    threads_path: Path | str | None = None,
) -> None:
    global _store, _store_config
    with _store_lock:
        _store = None
        _store_config = {
            "inspect_only": inspect_only,
            "threads_path": threads_path,
        }


def get_store() -> ThreadStore:
    global _store
    if _store is not None and _store.loaded:
        return _store
    with _store_lock:
        if _store is None or not _store.loaded:
            _store = ThreadStore(**_store_config)
            _store.load()
        return _store


class ClassificationUpdate(BaseModel):
    is_knowledge_bearing: bool | None = None
    topic_tags: list[str] | None = None
    entities: list[str] | None = None
    reason: str | None = None


class MergeRequest(BaseModel):
    thread_ids: list[str] = Field(min_length=2)
    survivor_id: str | None = None
    inherit_classification: str | None = None


class SplitRequest(BaseModel):
    source_id: str
    mode: Literal["sparse", "range"]
    message_indices: list[int] = Field(min_length=1)


class MoveMessagesRequest(BaseModel):
    source_id: str
    message_indices: list[int] = Field(min_length=1)
    target_id: str
    position: Literal["prepend", "append"]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    store = get_store()
    return store.meta()


@app.get("/api/taxonomy")
def taxonomy() -> dict[str, Any]:
    return {
        "categories": CATEGORIES,
        "pages": [
            {
                "id": p.id,
                "title_he": p.title_he,
                "category": p.category,
                "parent": p.parent,
            }
            for p in TAXONOMY
        ],
    }


@app.get("/api/threads")
def list_threads(
    filter: FilterKind = Query("useless"),
    sort: SortKind = Query("num_messages"),
    order: SortOrder = Query("desc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    num_messages_min: float | None = None,
    num_messages_max: float | None = None,
    participants_min: float | None = None,
    participants_max: float | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    start_month: str | None = None,
) -> dict[str, Any]:
    store = get_store()
    items, total = store.list_enriched(
        filter_kind=filter,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
        num_messages_min=num_messages_min,
        num_messages_max=num_messages_max,
        participants_min=participants_min,
        participants_max=participants_max,
        duration_min=duration_min,
        duration_max=duration_max,
        start_month=start_month,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/threads/{thread_id}")
def get_thread(
    thread_id: str,
    filter: FilterKind = Query("useless"),
    sort: SortKind = Query("num_messages"),
    order: SortOrder = Query("desc"),
) -> dict[str, Any]:
    store = get_store()
    thread = store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    classification = store.get_classification(thread_id)
    enriched = store._enrich(thread)
    return {
        "thread": thread,
        "classification": classification,
        "enriched": enriched,
        "neighbors": store.neighbors(thread_id),
        "queue": store.queue_neighbors(thread_id, filter, sort, order),
    }


@app.get("/api/stats")
def stats(filter: FilterKind = Query("all")) -> dict[str, Any]:
    store = get_store()
    items = [store._enrich(t) for t in store.threads]
    return compute_stats(items, filter_kind=filter)


@app.patch("/api/threads/{thread_id}/classification")
def update_classification(
    thread_id: str, body: ClassificationUpdate
) -> dict[str, Any]:
    store = get_store()
    if not store.has_classification:
        raise HTTPException(
            status_code=403,
            detail="classification data not loaded; tagging is unavailable",
        )
    if store.get_thread(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    try:
        record = store.update_classification(
            thread_id,
            is_knowledge_bearing=body.is_knowledge_bearing,
            topic_tags=body.topic_tags,
            entities=body.entities,
            reason=body.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="thread not found") from None
    return {"classification": record}


@app.post("/api/threads/merge")
def merge_threads(body: MergeRequest) -> dict[str, Any]:
    store = get_store()
    try:
        survivor_id = store.merge(
            body.thread_ids,
            survivor_id=body.survivor_id,
            inherit_classification=body.inherit_classification,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    thread = store.get_thread(survivor_id)
    classification = store.get_classification(survivor_id)
    return {
        "survivor_id": survivor_id,
        "thread": thread,
        "classification": classification,
    }


@app.post("/api/threads/split")
def split_thread_endpoint(body: SplitRequest) -> dict[str, Any]:
    store = get_store()
    try:
        result = store.split(body.source_id, body.mode, body.message_indices)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_id = result["new_thread_id"]
    thread = store.get_thread(new_id)
    classification = store.get_classification(new_id)
    remainder = None
    if result.get("remainder_id"):
        remainder = store.get_thread(result["remainder_id"])
    return {
        **result,
        "thread": thread,
        "classification": classification,
        "remainder": remainder,
    }


@app.post("/api/threads/move-messages")
def move_messages(body: MoveMessagesRequest) -> dict[str, Any]:
    store = get_store()
    try:
        store.move(
            body.source_id,
            body.message_indices,
            body.target_id,
            body.position,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result: dict[str, Any] = {
        "target_id": body.target_id,
        "target": store.get_thread(body.target_id),
        "target_classification": store.get_classification(body.target_id),
    }
    if store.get_thread(body.source_id):
        result["source_id"] = body.source_id
        result["source"] = store.get_thread(body.source_id)
        result["source_classification"] = store.get_classification(body.source_id)
    else:
        result["source_removed"] = body.source_id
    return result


def mount_static() -> None:
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
