"""The PII review store: claims whose contact details step 3 scrubbed, and the owner's decisions.

The store reads the claims artifact (C3): the edited file when it exists, else the original,
plus the original on its own (it keeps every claim's scrubbed text and ``_redactions``, which
a decision never touches, so a decision can be undone). It writes only the edited file; the
first decision creates it from what was loaded. Use it through a ``StoreHandle``: reads via
``handle.get()``, ``decide`` / ``undo`` inside ``handle.mutate()``.

Review fields of a claim:
- pending: ``_redactions`` = ``[{type, value}]``, one per ``REDACTION_MARK`` in ``claim_text``,
  in text order.
- reviewed: ``_redactions`` is gone, ``_pii_review`` is ``accepted`` (every mark stays),
  ``restored`` (every value is back in the text) or ``partial`` (some are), and
  ``_pii_reviewed_at`` is when (ISO-8601 UTC). A step-3 re-run that re-extracts the claim
  brings ``_redactions`` back, which makes it pending again.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from review_tool.fs import iso_utc
from step_3_extract.scrub import REDACTION_MARK, restore_scrubbed_text
from utils.json_io import write_json_file

FilterKind = Literal["pending", "reviewed", "all"]
SortKind = Literal["claim_id", "thread_id", "date", "redactions", "supporters"]
SortOrder = Literal["asc", "desc"]
Decision = Literal["accept", "restore"]

PENDING = "pending"
ACCEPTED = "accepted"
RESTORED = "restored"
PARTIAL = "partial"
REVIEWED = (ACCEPTED, RESTORED, PARTIAL)
EDITED_BY = "pii_reviewer"


class ClaimNotFound(LookupError):
    """The claim id is not a PII review item of the claims file (any more)."""


class ReviewConflict(ValueError):
    """The requested change does not fit the claim's current state."""


@dataclass(frozen=True)
class QueueView:
    """Which review items are listed, and in what order."""

    filter: FilterKind = "pending"
    sort: SortKind = "claim_id"
    order: SortOrder = "asc"


def review_status(claim: dict[str, Any]) -> str | None:
    """``pending``, one of ``REVIEWED``, or ``None`` for a claim that never had PII."""
    if claim.get("_redactions"):
        return PENDING
    status = claim.get("_pii_review")
    return status if status in REVIEWED else None


def _read_claims_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        raise ValueError(f"{path} is not a claims file: expected an object with a 'claims' list")
    return payload


def _segments(text: str, redaction_count: int) -> list[dict[str, Any]]:
    """The text split at its marks: ``{"text": ...}`` pieces and ``{"mark": i}`` (``i`` is the
    redaction the mark stands for, ``None`` for a mark without one)."""
    parts = text.split(REDACTION_MARK)
    segments: list[dict[str, Any]] = [{"text": parts[0]}] if parts[0] else []
    for index, part in enumerate(parts[1:]):
        segments.append({"mark": index if index < redaction_count else None})
        if part:
            segments.append({"text": part})
    return segments


class PiiReviewStore:
    """Every claim of the claims artifact, indexed for the review queue."""

    def __init__(self, original_path: Path, edited_path: Path) -> None:
        original = _read_claims_file(original_path)
        edited = _read_claims_file(edited_path)
        if original is None and edited is None:
            raise FileNotFoundError(
                f"{original_path} does not exist yet: run step 3 "
                "(python -m step_3_extract.run) first"
            )
        self._edited_path = edited_path
        self._source_path = edited_path if edited is not None else original_path
        # Without an edited file the payload starts as a copy of the original: the original's
        # claims must stay as on disk (``undo`` reads them back).
        self._payload: dict[str, Any] = edited if edited is not None else deepcopy(original)
        self._claims = {claim["claim_id"]: claim for claim in self._payload["claims"]}
        self._originals = (
            {claim["claim_id"]: claim for claim in original["claims"]} if original else {}
        )

    # ------------------------------------------------------------------ reading

    def summary(self) -> dict[str, Any]:
        """Counters of the review and which file the tool reads."""
        statuses = Counter(review_status(claim) for claim in self._claims.values())
        by_type = Counter(
            item["type"]
            for claim in self._claims.values()
            for item in claim.get("_redactions") or []
        )
        return {
            "file": self._source_path.name,
            "edited_exists": self._source_path == self._edited_path,
            "claims_count": len(self._claims),
            "review": {
                PENDING: statuses[PENDING],
                **{status: statuses[status] for status in REVIEWED},
                "total": sum(statuses[status] for status in (PENDING, *REVIEWED)),
            },
            "pending_by_type": dict(sorted(by_type.items())),
        }

    def page(self, view: QueueView, offset: int, limit: int) -> dict[str, Any]:
        """One page of the queue as list rows."""
        queue = self._queue(view)
        rows = [self._row(claim) for claim in queue[offset : offset + limit]]
        return {"items": rows, "total": len(queue), "offset": offset, "limit": limit}

    def detail(self, claim_id: str, view: QueueView) -> dict[str, Any]:
        """The claim with everything the decision needs, and its place in the queue."""
        claim = self._review_item(claim_id)
        return {"claim": self._detail(claim), "queue": self._neighbours(claim_id, view)}

    # ----------------------------------------------------------------- changing

    def decide(
        self,
        claim_id: str,
        decision: Decision,
        view: QueueView,
        restore: list[int] | None = None,
    ) -> dict[str, Any]:
        """Record a decision on a pending claim and save; return it and where to go next.

        ``accept`` keeps every mark. ``restore`` puts the values back: all of them, or only
        the marks numbered (from 0) in ``restore``. ``next_id`` is the first claim after this
        one in ``view`` that is still in it, else this claim if it stays in the view, else
        the first claim left in it (``None`` when the view is empty).
        """
        claim = self._review_item(claim_id)
        redactions = claim.get("_redactions") or []
        if not redactions:
            raise ReviewConflict(
                f"Claim {claim_id} has already been reviewed. To change the decision, undo it first."
            )
        chosen = self._restore_choice(claim, redactions, decision, restore)
        before = [item["claim_id"] for item in self._queue(view)]
        if chosen:
            claim["claim_text"] = restore_scrubbed_text(claim["claim_text"], redactions, chosen)
        claim["_pii_review"] = (
            ACCEPTED if not chosen else RESTORED if len(chosen) == len(redactions) else PARTIAL
        )
        claim["_pii_reviewed_at"] = iso_utc(time.time())
        del claim["_redactions"]
        self._save()
        return {
            "claim": self._detail(claim),
            "next_id": self._next_after(claim_id, before, view),
            "summary": self.summary(),
        }

    def undo(self, claim_id: str) -> dict[str, Any]:
        """Make a reviewed claim pending again, with the original file's text and redactions."""
        claim = self._review_item(claim_id)
        if review_status(claim) not in REVIEWED:
            raise ReviewConflict(f"Claim {claim_id} has not been reviewed yet; there is no decision to undo.")
        original = self._originals.get(claim_id)
        if not original or not original.get("_redactions"):
            raise ReviewConflict(
                f"Cannot undo: in the original file, claim {claim_id} has no removed details "
                "(step 3 may have run again and changed it)."
            )
        claim["claim_text"] = original["claim_text"]
        claim["_redactions"] = [dict(item) for item in original["_redactions"]]
        claim.pop("_pii_review", None)
        claim.pop("_pii_reviewed_at", None)
        self._save()
        return {"claim": self._detail(claim), "summary": self.summary()}

    # ---------------------------------------------------------------- internals

    def _review_item(self, claim_id: str) -> dict[str, Any]:
        claim = self._claims.get(claim_id)
        if claim is None or review_status(claim) is None:
            raise ClaimNotFound(
                f"Claim {claim_id} is not among the claims for privacy review "
                "(step 3 may have run again and removed it)."
            )
        return claim

    @staticmethod
    def _restore_choice(
        claim: dict[str, Any],
        redactions: list[dict[str, str]],
        decision: Decision,
        restore: list[int] | None,
    ) -> frozenset[int]:
        if decision == "accept":
            return frozenset()
        if claim["claim_text"].count(REDACTION_MARK) != len(redactions):
            raise ReviewConflict(
                f"In the text of {claim['claim_id']}, the number of {REDACTION_MARK} marks does not "
                "match the number of removed details, so there is no way to know where each "
                "detail goes back. Accept the removal or fix the text by hand."
            )
        chosen = frozenset(range(len(redactions)) if restore is None else restore)
        if not chosen or not chosen <= set(range(len(redactions))):
            raise ReviewConflict(
                f"Invalid choice of details to restore: {sorted(chosen)} "
                f"(the claim has {len(redactions)} details, numbered from 0)."
            )
        return chosen

    def _queue(self, view: QueueView) -> list[dict[str, Any]]:
        wanted = {
            "pending": (PENDING,),
            "reviewed": REVIEWED,
            "all": (PENDING, *REVIEWED),
        }[view.filter]
        items = [claim for claim in self._claims.values() if review_status(claim) in wanted]
        items.sort(key=lambda claim: claim["claim_id"])  # equal sort keys keep the id order
        items.sort(key=self._sort_key(view.sort), reverse=view.order == "desc")
        return items

    def _sort_key(self, sort: SortKind) -> Callable[[dict[str, Any]], Any]:
        keys: dict[str, Callable[[dict[str, Any]], Any]] = {
            "claim_id": lambda claim: claim["claim_id"],
            "thread_id": lambda claim: claim.get("thread_id") or "",
            "date": lambda claim: claim.get("first_date") or "",
            "redactions": lambda claim: len(self._redactions_of(claim)),
            "supporters": lambda claim: claim.get("supporter_count") or 0,
        }
        return keys[sort]

    def _neighbours(self, claim_id: str, view: QueueView) -> dict[str, Any]:
        ids = [claim["claim_id"] for claim in self._queue(view)]
        if claim_id not in ids:
            return {"position": None, "total": len(ids), "prev_id": None, "next_id": None}
        index = ids.index(claim_id)
        return {
            "position": index,
            "total": len(ids),
            "prev_id": ids[index - 1] if index > 0 else None,
            "next_id": ids[index + 1] if index + 1 < len(ids) else None,
        }

    def _next_after(self, claim_id: str, before: list[str], view: QueueView) -> str | None:
        after = [claim["claim_id"] for claim in self._queue(view)]
        remaining = set(after)
        following = before[before.index(claim_id) + 1 :] if claim_id in before else []
        for candidate in following:
            if candidate in remaining:
                return candidate
        if claim_id in remaining:
            return claim_id
        return after[0] if after else None

    def _redactions_of(self, claim: dict[str, Any]) -> list[dict[str, str]]:
        """Pending: the claim's own redactions. Reviewed: what the original file scrubbed."""
        if claim.get("_redactions"):
            return claim["_redactions"]
        return (self._originals.get(claim["claim_id"]) or {}).get("_redactions") or []

    def _row(self, claim: dict[str, Any]) -> dict[str, Any]:
        return {
            "claim_id": claim["claim_id"],
            "thread_id": claim.get("thread_id"),
            "text": claim.get("claim_text", ""),
            "status": review_status(claim),
            "redaction_counts": dict(
                sorted(Counter(item["type"] for item in self._redactions_of(claim)).items())
            ),
            "first_date": claim.get("first_date"),
            "last_date": claim.get("last_date"),
            "supporter_count": claim.get("supporter_count"),
            "opposer_count": claim.get("opposer_count"),
        }

    def _detail(self, claim: dict[str, Any]) -> dict[str, Any]:
        status = review_status(claim)
        text = claim.get("claim_text", "")
        redactions = self._redactions_of(claim)
        original = self._originals.get(claim["claim_id"]) or {}
        # The scrubbed text the marks belong to: the claim's own while pending, the
        # original file's once reviewed (by then the edited text may hold restored values).
        scrubbed = text if status == PENDING else original.get("claim_text", "")
        return {
            **self._row(claim),
            "entities": claim.get("entities") or [],
            "stance": claim.get("stance"),
            "supporting_message_count": len(claim.get("supporting_message_ids") or []),
            "opposing_message_count": len(claim.get("opposing_message_ids") or []),
            "redactions": [
                {**item, "in_text": status != PENDING and item["value"] in text}
                for item in redactions
            ],
            "segments": _segments(scrubbed, len(redactions)) if redactions else [],
            "marks_match": scrubbed.count(REDACTION_MARK) == len(redactions),
            "reviewed_at": claim.get("_pii_reviewed_at"),
            "can_undo": status in REVIEWED and bool(original.get("_redactions")),
        }

    def _save(self) -> None:
        metadata = self._payload.setdefault("metadata", {})
        metadata["edited_by"] = EDITED_BY
        metadata["edited_at"] = iso_utc(time.time())
        metadata["pii_review"] = self.summary()["review"]
        write_json_file(self._payload, self._edited_path)
        self._source_path = self._edited_path
