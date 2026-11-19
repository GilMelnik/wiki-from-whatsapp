"""The evidence the suggestions tool shows next to a suggestion, read from the wiki as it is
NOW (effective pages, claim records, plan and assignments: ``step_8_review.inputs.ReviewInput``).

- mistake: the quoted sentence inside its paragraph of the current page (``quote``: the
  paragraph as a reader reads it and where the sentence sits in it, ``None`` when the page no
  longer has it), and the claims it rests on;
- contradiction: the claims of each side;
- structural: the pages involved with their scope and size; for a new page also its
  category's pages, for a new label the labels of the same kind.

A claim is shown by its short id, text, stance, supporters / opposers, dates and the page
sentences that cite it; never by its claim or thread id (WhatsApp message ids) or any identity
(entity names are left out as well).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from review_tool.tools.suggestions.apply import sentences
from step_6_plan.model import iter_nodes
from step_6_plan.operation_requests import title_of
from step_8_review.inputs import ReviewInput
from step_8_review.proposals import CONTRADICTION, MISTAKE, effective_proposal, plain_text

_PARAGRAPH = re.compile(r"\n\s*\n")


class WikiView:
    """One loaded wiki (a ``ReviewInput``) and the evidence of any suggestion against it."""

    def __init__(self, review: ReviewInput) -> None:
        self.review = review
        self.plan = review.plan
        self._sentences = {pid: sentences(page.body) for pid, page in review.pages.items()}

    # ------------------------------------------------------------------ pieces

    def page(self, page_id: str) -> dict[str, Any]:
        """A page of the plan: title, description, scope, where it sits, and its size (claims
        whose main page it is / that only mention it, words of its text)."""
        planned = self.plan["pages"].get(page_id)
        if planned is None:
            return {"page_id": page_id, "exists": False}
        corpus = self.review.corpus
        on_page = corpus.page_claims(page_id)
        main = sum(1 for sid in on_page if corpus.primary_page(sid) == page_id)
        written = self.review.pages.get(page_id)
        return {
            "page_id": page_id, "exists": True, "written": written is not None,
            "title": planned["title_he"], "description": planned["description"],
            "scope": planned["scope"],
            "path": [title_of(self.plan, node_id) for node_id in planned["path"]],
            "claims": {"main": main, "mention": len(on_page) - main},
            "words": len(plain_text(written.body).split()) if written else 0,
        }

    def said_in(self, short_id: str) -> list[dict[str, Any]]:
        """The sentences of every page that cite the claim, as a reader reads them."""
        mark = f"[^{short_id}]"
        found = []
        for page_id, page_sentences in self._sentences.items():
            cited = [plain_text(sentence) for sentence in page_sentences if mark in sentence]
            if cited:
                found.append({"page_id": page_id, "title": self.review.pages[page_id].title,
                              "sentences": cited})
        return found

    def claim(self, short_id: str) -> dict[str, Any]:
        """One claim as the owner judges it (``missing`` when it is gone)."""
        claim = self.review.corpus.claims.get(short_id)
        if claim is None:
            return {"short_id": short_id, "missing": True}
        primary = self.review.corpus.primary_page(short_id)
        return {
            "short_id": short_id, "claim_text": " ".join(claim["claim_text"].split()),
            "stance": claim["stance"], "supporters": claim["supporter_count"],
            "opposers": claim["opposer_count"], "first_date": claim["first_date"],
            "last_date": claim["last_date"], "primary_page": primary,
            "primary_title": title_of(self.plan, primary) if primary else None,
            "said_in": self.said_in(short_id),
        }

    def claims(self, short_ids: Iterable[str]) -> list[dict[str, Any]]:
        return [self.claim(sid) for sid in short_ids]

    def quote(self, page_id: str, sentence: str) -> dict[str, Any] | None:
        """The paragraph of the current page holding ``sentence`` (as a reader reads it) and
        the sentence's place in it; ``None`` when the page no longer has it."""
        page = self.review.pages.get(page_id)
        target = plain_text(sentence)
        if page is None or not target:
            return None
        for paragraph in _PARAGRAPH.split(page.body):
            text = plain_text(paragraph)
            start = text.find(target)
            if start >= 0:
                return {"text": text, "start": start, "end": start + len(target)}
        return None

    # ------------------------------------------------------------------ per kind

    def evidence(self, suggestion: Mapping[str, Any]) -> dict[str, Any]:
        """Everything the owner needs to judge ``suggestion`` (its effective proposal)."""
        kind = suggestion["kind"]
        proposal = effective_proposal(suggestion)
        if kind == MISTAKE:
            return {"page": self.page(proposal["page_id"]),
                    "quote": self.quote(proposal["page_id"], proposal["sentence"]),
                    "claims": self.claims(proposal["claims"])}
        if kind == CONTRADICTION:
            return {"sides": {"a": self.claims(proposal["claims_a"]),
                              "b": self.claims(proposal["claims_b"])},
                    "pages": [self.page(pid) for pid in proposal["pages"]]}
        return self._structural(kind, proposal, suggestion["pages"])

    def _structural(self, kind: str, proposal: Mapping[str, Any],
                    named: Iterable[str]) -> dict[str, Any]:
        involved = {"split": [proposal.get("page_id", "")],
                    "merge": [proposal.get("survivor_id", ""), *proposal.get("merged_ids", [])]}
        page_ids = list(dict.fromkeys([*involved.get(kind, []), *named]))
        evidence: dict[str, Any] = {"pages": [self.page(pid) for pid in page_ids if pid]}
        if kind == "add_page":
            parent_id = proposal.get("parent_id", "")
            evidence["parent"] = {"id": parent_id, "title": title_of(self.plan, parent_id),
                                  "pages": [self.page(pid) for pid in self._pages_under(parent_id)]}
        if kind == "add_label":
            evidence["labels"] = [
                {"label_id": lid, "title": label["title_he"], "description": label["description"]}
                for lid, label in self.plan["labels"].items()
                if label["kind"] == proposal.get("kind")]
        return evidence

    def _pages_under(self, node_id: str) -> list[str]:
        """The pages directly under a category of the plan, in tree order."""
        return [node["id"] for node, ancestors in iter_nodes(self.plan["tree"])
                if node["id"] in self.plan["pages"] and ancestors[-1:] == (node_id,)]
