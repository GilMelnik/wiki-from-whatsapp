"""Structured, traceable wiki page store for the community agent (step 7).

Pages hold sections of *statements*; every statement records the ``claim_ids`` it
is based on. Supporter counts are always recomputed from the private audit by
unioning supporter identities across a statement's claim_ids, so re-citing a
claim is idempotent and no supporter is ever double-counted.

Markdown rendering exposes supporter counts only — never claim_ids or member
identities. The structured JSON (``data/wiki_pages.json``) is the traceable
sidecar mapping every sentence back to its claims.
"""

from __future__ import annotations

import re
from typing import Any

from utils.support import supporter_count_for_claims

STANCE_HE = {
    "positive": "חיובי",
    "negative": "שלילי",
    "neutral": "ניטרלי",
    "factual": "עובדתי",
}

_STATEMENT_ID_RE = re.compile(r"-s(\d+)$")


def _support_annotation(statement: dict[str, Any]) -> str:
    """Human-readable supporter line; counts are computed, never LLM-authored."""

    count = statement.get("supporter_count", 0)
    breakdown = statement.get("stance_breakdown") or {}
    pos = breakdown.get("positive", 0)
    neg = breakdown.get("negative", 0)
    parts = [f"{count} תומכים ייחודיים"]
    if pos and neg:
        parts.append(f"בעד: {pos} · נגד: {neg}")
    return f"({' · '.join(parts)})"


class PageStore:
    """In-memory wiki page store with deterministic supporter accounting."""

    def __init__(
        self,
        claims_by_id: dict[str, dict[str, Any]] | None = None,
        audit_by_id: dict[str, dict[str, Any]] | None = None,
    ):
        self.pages: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, str]] = []
        self.claims_by_id = claims_by_id or {}
        self.audit_by_id = audit_by_id or {}
        self._counters: dict[str, int] = {}

    # ------------------------------------------------------------------ pages
    def ensure_page(
        self,
        page_id: str,
        *,
        title: str | None = None,
        category: str = "emergent",
        parent: str | None = None,
        source_tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        page_id = str(page_id or "").strip()
        if not page_id:
            return None
        page = self.pages.get(page_id)
        if page is None:
            page = {
                "id": page_id,
                "title": title or page_id,
                "category": category or "emergent",
                "parent": parent,
                "source_tags": list(source_tags or [page_id]),
                "sections": [],
                "related_pages": [],
            }
            self.pages[page_id] = page
        elif title:
            page["title"] = title
        return page

    # ------------------------------------------------------------- statements
    def _next_statement_id(self, page_id: str) -> str:
        self._counters[page_id] = self._counters.get(page_id, 0) + 1
        return f"{page_id}-s{self._counters[page_id]}"

    def _valid_claim_ids(self, claim_ids: list[str] | None) -> list[str]:
        out: list[str] = []
        for cid in claim_ids or []:
            cid = str(cid).strip()
            if cid and cid in self.claims_by_id and cid not in out:
                out.append(cid)
        return out

    def _section(self, page: dict[str, Any], heading: str | None) -> dict[str, Any]:
        heading = (heading or "").strip()
        for sec in page["sections"]:
            if sec["heading"] == heading:
                return sec
        sec = {"heading": heading, "statements": []}
        page["sections"].append(sec)
        return sec

    def _find_statement(
        self, page: dict[str, Any], statement_id: str
    ) -> dict[str, Any] | None:
        for sec in page["sections"]:
            for st in sec["statements"]:
                if st["statement_id"] == statement_id:
                    return st
        return None

    def _recompute(self, statement: dict[str, Any]) -> None:
        statement["supporter_count"] = supporter_count_for_claims(
            statement["claim_ids"], self.audit_by_id
        )
        breakdown: dict[str, int] = {}
        for cid in statement["claim_ids"]:
            stance = (self.claims_by_id.get(cid) or {}).get("stance", "neutral")
            breakdown[stance] = breakdown.get(stance, 0) + 1
        statement["stance_breakdown"] = breakdown

    def upsert_statement(
        self,
        page_id: str,
        section: str | None,
        statement_id: str | None,
        text: str | None,
        claim_ids: list[str] | None,
    ) -> dict[str, Any] | None:
        page = self.ensure_page(page_id)
        if page is None:
            return None
        text = (text or "").strip()
        claim_ids = self._valid_claim_ids(claim_ids)
        # Every statement must be backed by at least one real claim.
        if not text or not claim_ids:
            return None

        existing = (
            self._find_statement(page, str(statement_id)) if statement_id else None
        )
        if existing is not None:
            existing["text"] = text
            merged = list(existing["claim_ids"])
            for cid in claim_ids:
                if cid not in merged:
                    merged.append(cid)
            existing["claim_ids"] = merged
            self._recompute(existing)
            return existing

        statement = {
            "statement_id": self._next_statement_id(page["id"]),
            "text": text,
            "claim_ids": claim_ids,
        }
        self._recompute(statement)
        self._section(page, section)["statements"].append(statement)
        return statement

    # ----------------------------------------------------------------- links
    def add_link(self, src: str, dst: str, reason: str = "") -> None:
        src, dst = str(src or "").strip(), str(dst or "").strip()
        if not src or not dst or src == dst:
            return
        if src not in self.pages or dst not in self.pages:
            return
        if any(link["from"] == src and link["to"] == dst for link in self.links):
            return
        self.links.append({"from": src, "to": dst, "reason": str(reason or "")})

    def set_related(self, page_id: str, related: list[str] | None) -> None:
        page = self.pages.get(str(page_id or "").strip())
        if not page:
            return
        out: list[str] = []
        for rid in related or []:
            rid = str(rid).strip()
            if rid and rid in self.pages and rid != page["id"] and rid not in out:
                out.append(rid)
        page["related_pages"] = out

    # ------------------------------------------------------------------ views
    def catalog(self, exclude: str | None = None) -> str:
        lines = [
            f"- {pid} — {page['title']}"
            for pid, page in self.pages.items()
            if pid != exclude
        ]
        return "\n".join(lines) if lines else "(אין עמודים נוספים)"

    def page_view(self, page_id: str) -> dict[str, Any]:
        page = self.pages.get(page_id)
        if not page:
            return {"id": page_id, "title": page_id, "sections": [], "related_pages": []}
        return {
            "id": page["id"],
            "title": page["title"],
            "sections": [
                {
                    "heading": sec["heading"],
                    "statements": [
                        {
                            "statement_id": st["statement_id"],
                            "text": st["text"],
                            "claim_ids": st["claim_ids"],
                            "supporter_count": st["supporter_count"],
                        }
                        for st in sec["statements"]
                    ],
                }
                for sec in page["sections"]
            ],
            "related_pages": page.get("related_pages", []),
        }

    def render_community(self, page_id: str) -> str:
        """Render a page's community body to Markdown (counts only, no claim ids)."""

        page = self.pages.get(page_id)
        if not page:
            return ""
        parts: list[str] = []
        for sec in page["sections"]:
            if sec["heading"]:
                parts.append(f"### {sec['heading']}")
            for st in sec["statements"]:
                annotation = _support_annotation(st)
                parts.append(f"{st['text'].strip()} {annotation}".strip())
        return "\n\n".join(p for p in parts if p)

    def statement_count(self) -> int:
        return sum(
            len(sec["statements"])
            for page in self.pages.values()
            for sec in page["sections"]
        )

    # --------------------------------------------------------------------- io
    def to_payload(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"pages": self.pages, "links": self.links, "metadata": metadata or {}}

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        claims_by_id: dict[str, dict[str, Any]] | None = None,
        audit_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> "PageStore":
        store = cls(claims_by_id, audit_by_id)
        store.pages = payload.get("pages") or {}
        store.links = payload.get("links") or []
        for pid, page in store.pages.items():
            highest = 0
            for sec in page.get("sections", []):
                for st in sec.get("statements", []):
                    match = _STATEMENT_ID_RE.search(st.get("statement_id", ""))
                    if match:
                        highest = max(highest, int(match.group(1)))
            store._counters[pid] = highest
        return store
