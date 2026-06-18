"""Load, review, and persist scrubbed claims for PII review."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from utils import write_json_file
from wiki_build.claims_paths import (
    BACKUPS_DIR,
    EDITED_CLAIMS_PATH,
    init_claims_edited,
    resolve_claims_path,
)
from wiki_build.scrub import REDACTION_MARK, restore_scrubbed_text, summarize_redactions

FilterKind = Literal["pending", "reviewed", "all"]
ReviewDecision = Literal["accept", "restore"]
SortKind = Literal["claim_id", "thread_id", "date", "redactions"]
SortOrder = Literal["asc", "desc"]


def _review_status(claim: dict[str, Any]) -> str:
    if claim.get("_redactions"):
        return "pending"
    return str(claim.get("_pii_review") or "")


def _is_review_item(claim: dict[str, Any]) -> bool:
    return bool(claim.get("_redactions") or claim.get("_pii_review"))


def _enrich_claim(claim: dict[str, Any]) -> dict[str, Any]:
    redactions = claim.get("_redactions") or []
    scrubbed = claim.get("claim_text", "")
    original = (
        restore_scrubbed_text(scrubbed, redactions) if redactions else scrubbed
    )
    return {
        "claim_id": claim["claim_id"],
        "thread_id": claim.get("thread_id"),
        "claim_text": scrubbed,
        "original_text": original,
        "topic_tags": claim.get("topic_tags") or [],
        "entities": claim.get("entities") or [],
        "stance": claim.get("stance"),
        "date": claim.get("date"),
        "support_count": claim.get("support_count"),
        "statement_count": claim.get("statement_count"),
        "reaction_endorser_count": claim.get("reaction_endorser_count"),
        "redactions": redactions,
        "redaction_summary": summarize_redactions(redactions),
        "review_status": _review_status(claim),
        "pii_review": claim.get("_pii_review"),
        "pii_reviewed_at": claim.get("_pii_reviewed_at"),
        "has_redaction_mark": REDACTION_MARK in scrubbed,
    }


class ClaimStore:
    def __init__(self, *, claims_path: Path | str | None = None) -> None:
        self._claims_path_override = (
            Path(claims_path) if claims_path is not None else None
        )
        self._payload: dict[str, Any] | None = None
        self._claims_by_id: dict[str, dict[str, Any]] = {}
        self._backup_done = False
        self._loaded = False
        self._source_path = resolve_claims_path()

    def load(self) -> None:
        if self._claims_path_override is not None:
            self._source_path = self._claims_path_override
            if not self._source_path.is_file():
                raise FileNotFoundError(
                    f"Claims file not found: {self._source_path}"
                )
        else:
            init_claims_edited()
            self._source_path = resolve_claims_path()

        with self._source_path.open(encoding="utf-8") as f:
            self._payload = json.load(f)

        self._claims_by_id = {
            c["claim_id"]: c for c in self._payload.get("claims", [])
        }
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def claims(self) -> list[dict[str, Any]]:
        if not self._loaded or self._payload is None:
            raise RuntimeError("ClaimStore.load() has not completed")
        return self._payload["claims"]

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        return self._claims_by_id.get(claim_id)

    def enrich(self, claim: dict[str, Any]) -> dict[str, Any]:
        return _enrich_claim(claim)

    def get_enriched(self, claim_id: str) -> dict[str, Any] | None:
        claim = self.get_claim(claim_id)
        if claim is None or not _is_review_item(claim):
            return None
        return _enrich_claim(claim)

    def list_enriched(
        self,
        filter_kind: FilterKind = "pending",
        sort: SortKind = "claim_id",
        order: SortOrder = "asc",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        items = [_enrich_claim(c) for c in self.claims if _is_review_item(c)]

        if filter_kind == "pending":
            items = [i for i in items if i["review_status"] == "pending"]
        elif filter_kind == "reviewed":
            items = [i for i in items if i["review_status"] in ("accepted", "restored")]

        reverse = order == "desc"

        def sort_key(item: dict[str, Any]) -> Any:
            if sort == "thread_id":
                return item.get("thread_id") or ""
            if sort == "date":
                return item.get("date") or ""
            if sort == "redactions":
                return len(item.get("redactions") or [])
            return item["claim_id"]

        items.sort(key=sort_key, reverse=reverse)
        total = len(items)
        return items[offset : offset + limit], total

    def queue_neighbors(
        self,
        claim_id: str,
        filter_kind: FilterKind,
        sort: SortKind,
        order: SortOrder,
    ) -> dict[str, str | None]:
        items, _ = self.list_enriched(
            filter_kind=filter_kind, sort=sort, order=order, limit=100000
        )
        ids = [i["claim_id"] for i in items]
        if claim_id not in ids:
            return {"prev_in_queue": None, "next_in_queue": None}
        idx = ids.index(claim_id)
        return {
            "prev_in_queue": ids[idx - 1] if idx > 0 else None,
            "next_in_queue": ids[idx + 1] if idx < len(ids) - 1 else None,
        }

    def review(self, claim_id: str, decision: ReviewDecision) -> dict[str, Any]:
        claim = self._claims_by_id.get(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        if not claim.get("_redactions"):
            raise ValueError("claim has no pending redactions")

        updated = deepcopy(claim)
        if decision == "restore":
            updated["claim_text"] = restore_scrubbed_text(
                updated.get("claim_text", ""),
                updated["_redactions"],
            )
            updated["_pii_review"] = "restored"
        else:
            updated["_pii_review"] = "accepted"

        updated.pop("_redactions", None)
        updated["_pii_reviewed_at"] = datetime.now().isoformat(timespec="seconds")

        self._claims_by_id[claim_id] = updated
        self._payload["claims"] = [
            self._claims_by_id[c["claim_id"]] for c in self._payload["claims"]
        ]
        self.save()
        return _enrich_claim(updated)

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        if self._source_path.exists():
            dest = BACKUPS_DIR / f"{self._source_path.stem}_{ts}{self._source_path.suffix}"
            shutil.copy2(self._source_path, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._payload is not None
        self._ensure_backup()

        if self._claims_path_override is not None:
            write_json_file(self._payload, self._source_path)
            return

        meta = self._payload.setdefault("metadata", {})
        meta["edited_by"] = "pii_reviewer"
        meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
        meta["pii_review"] = self._review_summary()
        write_json_file(self._payload, EDITED_CLAIMS_PATH)
        self._source_path = EDITED_CLAIMS_PATH

    def _review_summary(self) -> dict[str, int]:
        pending = accepted = restored = 0
        for claim in self.claims:
            status = _review_status(claim)
            if status == "pending":
                pending += 1
            elif status == "accepted":
                accepted += 1
            elif status == "restored":
                restored += 1
        return {
            "pending": pending,
            "accepted": accepted,
            "restored": restored,
            "total_flagged": pending + accepted + restored,
        }

    def meta(self) -> dict[str, Any]:
        summary = self._review_summary()
        return {
            "claims_path": str(self._source_path),
            "claims_count": len(self.claims),
            "review": summary,
        }

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for claim in self.claims:
            for item in claim.get("_redactions") or []:
                by_type[item["type"]] = by_type.get(item["type"], 0) + 1
        return {
            "review": self._review_summary(),
            "pending_redactions_by_type": by_type,
        }
