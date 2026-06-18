"""FastAPI server for the PII claims review tool."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pii_reviewer.store import ClaimStore, FilterKind, ReviewDecision, SortKind, SortOrder

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="PII Claims Reviewer", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: ClaimStore | None = None
_store_config: dict[str, Any] = {"claims_path": None}
_store_lock = threading.Lock()


def configure_store(*, claims_path: Path | str | None = None) -> None:
    global _store, _store_config
    with _store_lock:
        _store = None
        _store_config = {"claims_path": claims_path}


def get_store() -> ClaimStore:
    global _store
    if _store is not None and _store.loaded:
        return _store
    with _store_lock:
        if _store is None or not _store.loaded:
            _store = ClaimStore(**_store_config)
            _store.load()
        return _store


class ReviewRequest(BaseModel):
    decision: Literal["accept", "restore"]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return get_store().meta()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return get_store().stats()


@app.get("/api/claims")
def list_claims(
    filter: FilterKind = Query("pending"),
    sort: SortKind = Query("claim_id"),
    order: SortOrder = Query("asc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    items, total = store.list_enriched(
        filter_kind=filter,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/claims/{claim_id}")
def get_claim(
    claim_id: str,
    filter: FilterKind = Query("pending"),
    sort: SortKind = Query("claim_id"),
    order: SortOrder = Query("asc"),
) -> dict[str, Any]:
    store = get_store()
    item = store.get_enriched(claim_id)
    if item is None:
        raise HTTPException(status_code=404, detail="claim not found")
    return {
        "claim": item,
        "queue": store.queue_neighbors(claim_id, filter, sort, order),
    }


@app.post("/api/claims/{claim_id}/review")
def review_claim(claim_id: str, body: ReviewRequest) -> dict[str, Any]:
    store = get_store()
    try:
        claim = store.review(claim_id, body.decision)
    except KeyError:
        raise HTTPException(status_code=404, detail="claim not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"claim": claim, "meta": store.meta()}


def mount_static() -> None:
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
