"""FastAPI server for the PII claims review tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel

from step_4_extract.reviewer.store import ClaimStore, FilterKind, SortKind, SortOrder
from utils.reviewer_server import StoreRegistry, make_reviewer_app, mount_static as _mount_static

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = make_reviewer_app("PII Claims Reviewer")
_registry = StoreRegistry(ClaimStore, {"claims_path": None})


def configure_store(*, claims_path: Path | str | None = None) -> None:
    _registry.configure(claims_path=claims_path)


def get_store() -> ClaimStore:
    return _registry.get()


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
    _mount_static(app, STATIC_DIR)
