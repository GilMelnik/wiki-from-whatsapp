"""Review suggestions (contract C12): their kinds, the shape of each kind's ``proposal``, the
checks a proposal must pass, and how a model answer becomes stored suggestions.

A stored suggestion::

    {"id": "sug-<sha1[:8]>", "kind": "split|merge|add_page|add_label|contradiction|mistake",
     "pages": [page_id], "reason": Hebrew, "proposal": {...}, "status": "pending|accepted|
     rejected|applied", "edited_proposal"?: {...},
     # additive: "title" (Hebrew headline), "raised_at", "raised_on" {page_id: fingerprint of
     # the page when raised}, "page_marks" {page_id: "changed" | "gone"} (refreshed by every
     # run), "applied_at" and "apply_result" (written when applied)}

``proposal`` per kind (what code carries out; ``edited_proposal`` has the same shape):

- ``split`` / ``merge`` / ``add_page`` / ``add_label``: one plan operation in the request shape
  the plan tool validates (``step_6_plan.reviewer.requests``): ``{"op": "split", page_id,
  new_page_id, title_he, description, scope, original_scope?}``, ``{"op": "merge",
  survivor_id, merged_ids}``, ``{"op": "add_page", page_id, parent_id, title_he, description,
  scope}``, ``{"op": "add_label", label_id, title_he, description, kind}``. New ids are the
  model's suggestion; the owner may correct them.
- ``contradiction``: ``{groups_a, groups_b, pages, clash, correction}``: the groups on each
  side, every page that must show both sides, one sentence on what clashes, and how the pages
  should present it (Hebrew instruction to the page writer).
- ``mistake``: ``{page_id, sentence, group_ids, correction}``: the sentence as it stands in the
  page, the groups that show the mistake (on this page or, for text that belongs elsewhere,
  on the page that owns it), and the correction as an instruction.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from step_6_plan.model import PlanError
from step_6_plan.reviewer.requests import Operation
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput
from utils.wiki_links import replace_wiki_links

STRUCTURAL = {"split": "split", "merge": "merge", "add_page": "add_page", "add_label": "add_label"}
CONTRADICTION, MISTAKE = "contradiction", "mistake"
KINDS = (*STRUCTURAL, CONTRADICTION, MISTAKE)
PENDING, ACCEPTED, REJECTED, APPLIED = "pending", "accepted", "rejected", "applied"
STATUSES = (PENDING, ACCEPTED, REJECTED, APPLIED)
OPEN = (PENDING, ACCEPTED)  # still to be decided or carried out: page marks matter
OPERATION = TypeAdapter(Operation)

_TEXT = {"type": "string"}
_IDS = {"type": "array", "items": _TEXT}
# The flat answer of one suggestion: every kind's fields, "" / [] where they do not apply.
ANSWER_FIELDS: dict[str, dict[str, Any]] = {
    "kind": {"type": "string", "enum": list(KINDS)},
    "title": _TEXT, "pages": _IDS, "reason": _TEXT,
    "page_id": _TEXT, "new_page_id": _TEXT, "survivor_id": _TEXT, "merged_ids": _IDS,
    "parent_id": _TEXT, "label_id": _TEXT, "label_kind": _TEXT, "title_he": _TEXT,
    "description": _TEXT, "scope": _TEXT, "original_scope": _TEXT,
    "groups_a": _IDS, "groups_b": _IDS, "clash": _TEXT,
    "sentence": _TEXT, "group_ids": _IDS, "correction": _TEXT,
}
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": _TEXT,
        "suggestions": {"type": "array", "items": {
            "type": "object", "properties": ANSWER_FIELDS, "required": list(ANSWER_FIELDS),
            "additionalProperties": False}},
    },
    "required": ["note", "suggestions"],
    "additionalProperties": False,
}

_MARK = re.compile(r"\[\^[^\]\s]+\](?!:)")
_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'"})


def plain_text(text: str) -> str:
    """Text as a reader reads it: no citation marks, links as their words, one space."""
    text = _MARK.sub("", replace_wiki_links(text, lambda link: None))
    return " ".join(text.translate(_QUOTES).replace("*", "").split()).strip()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _ids(value: Any) -> list[str]:
    return list(dict.fromkeys(_clean(v) for v in value or [] if _clean(v)))


def proposal_from_answer(kind: str, answer: Mapping[str, Any]) -> dict[str, Any]:
    """The stored ``proposal`` of one flat answer (see the module docstring)."""
    text = {key: _clean(answer.get(key)) for key in ANSWER_FIELDS if ANSWER_FIELDS[key] is _TEXT}
    if kind == "split":
        proposal = {"op": "split", "page_id": text["page_id"], "new_page_id": text["new_page_id"],
                    "title_he": text["title_he"], "description": text["description"],
                    "scope": text["scope"]}
        if text["original_scope"]:
            proposal["original_scope"] = text["original_scope"]
        return proposal
    if kind == "merge":
        return {"op": "merge", "survivor_id": text["survivor_id"],
                "merged_ids": _ids(answer.get("merged_ids"))}
    if kind == "add_page":
        return {"op": "add_page", "page_id": text["page_id"], "parent_id": text["parent_id"],
                "title_he": text["title_he"], "description": text["description"],
                "scope": text["scope"]}
    if kind == "add_label":
        return {"op": "add_label", "label_id": text["label_id"], "title_he": text["title_he"],
                "description": text["description"], "kind": text["label_kind"]}
    if kind == CONTRADICTION:
        return {"groups_a": _ids(answer.get("groups_a")), "groups_b": _ids(answer.get("groups_b")),
                "pages": _ids(answer.get("pages")), "clash": text["clash"],
                "correction": text["correction"]}
    return {"page_id": text["page_id"], "sentence": str(answer.get("sentence") or "").strip(),
            "group_ids": _ids(answer.get("group_ids")), "correction": text["correction"]}


def suggestion_pages(kind: str, proposal: Mapping[str, Any], named: Sequence[str]) -> list[str]:
    """The pages a suggestion is about: what its operation touches, else the pages named."""
    if kind == "split" or kind == MISTAKE:
        return [proposal["page_id"]]
    if kind == "merge":
        return list(dict.fromkeys([proposal["survivor_id"], *proposal["merged_ids"]]))
    if kind == CONTRADICTION:
        return list(proposal["pages"])
    return list(dict.fromkeys(named))


def operation(kind: str, proposal: Mapping[str, Any]) -> Operation:
    """The plan operation of a structural proposal (``ValueError`` if it is not one)."""
    try:
        op = OPERATION.validate_python(dict(proposal))
    except ValidationError as exc:
        raise ValueError(f"not a valid plan operation: {exc.errors(include_url=False)}") from exc
    if op.op != STRUCTURAL[kind]:
        raise ValueError(f"a {kind} suggestion is carried out by the operation "
                         f"{STRUCTURAL[kind]!r}, not {op.op!r}")
    return op


def _missing(proposal: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    return [f"{key} is empty" for key in keys if not proposal.get(key)]


def problems(kind: str, proposal: Mapping[str, Any], review: ReviewInput) -> list[str]:
    """Why ``proposal`` cannot be carried out on the current wiki (empty: it can)."""
    if kind in STRUCTURAL:
        try:
            operation(kind, proposal).apply(review.plan)
        except ValueError as exc:
            return [str(exc)]
        except PlanError as exc:
            return list(exc.errors)
        return []
    if kind == MISTAKE:
        found = _missing(proposal, ("page_id", "sentence", "group_ids", "correction"))
        page = review.pages.get(proposal.get("page_id", ""))
        if page is None:
            return [*found, f"page {proposal.get('page_id')!r} is not a written page of the plan"]
        if proposal.get("sentence") and plain_text(proposal["sentence"]) not in plain_text(page.body):
            found.append(f"the sentence is not in page {page.page_id}: {proposal['sentence']!r}")
        unknown = [gid for gid in proposal.get("group_ids") or [] if gid not in review.corpus.groups]
        if unknown:
            found.append(f"unknown groups: {unknown}")
        return found
    found = _missing(proposal, ("groups_a", "groups_b", "pages", "clash", "correction"))
    side_a, side_b = set(proposal.get("groups_a") or []), set(proposal.get("groups_b") or [])
    if side_a & side_b:
        found.append(f"groups on both sides: {sorted(side_a & side_b)}")
    unknown_pages = [pid for pid in proposal.get("pages") or [] if pid not in review.pages]
    if unknown_pages:
        found.append(f"pages that are not written pages of the plan: {unknown_pages}")
    on_pages = {gid for pid in proposal.get("pages") or [] if pid in review.pages
                for gid in review.pages[pid].group_ids}
    stray = sorted(gid for gid in side_a | side_b if gid not in on_pages)
    if stray:
        found.append(f"groups on none of the suggestion's pages: {stray}")
    if len(side_a) * len(side_b) > REVIEW_CONFIG["apply"]["max_link_pairs"]:
        found.append(f"{len(side_a)} x {len(side_b)} group pairs; at most "
                     f"{REVIEW_CONFIG['apply']['max_link_pairs']} links per suggestion")
    return found


def effective_proposal(suggestion: Mapping[str, Any]) -> dict[str, Any]:
    """What gets carried out: the owner's edited proposal if there is one."""
    return dict(suggestion.get("edited_proposal") or suggestion["proposal"])


def signatures(kind: str, proposal: Mapping[str, Any]) -> set[tuple[str, ...]]:
    """Keys that identify the same suggestion raised again, in other words."""
    if kind == MISTAKE:
        return {(kind, proposal.get("page_id", ""), plain_text(proposal.get("sentence", "")))}
    if kind == CONTRADICTION:
        groups = sorted({*proposal.get("groups_a", []), *proposal.get("groups_b", [])})
        return {(kind, *groups)}
    if kind == "split":
        return {(kind, proposal.get("page_id", ""))}
    if kind == "merge":
        return {(kind, *sorted({proposal.get("survivor_id", ""), *proposal.get("merged_ids", [])}))}
    new_id = proposal.get("page_id" if kind == "add_page" else "label_id", "")
    name = plain_text(proposal.get("title_he", "")).casefold()
    return {(kind, new_id), (kind, "title", name)} if name else {(kind, new_id)}


def raised_signatures(suggestions: Iterable[Mapping[str, Any]]) -> set[tuple[str, ...]]:
    """Every signature of every suggestion already raised (original and edited proposal)."""
    keys: set[tuple[str, ...]] = set()
    for suggestion in suggestions:
        for proposal in (suggestion["proposal"], suggestion.get("edited_proposal")):
            if proposal:
                keys |= signatures(suggestion["kind"], proposal)
    return keys


def new_id(kind: str, proposal: Mapping[str, Any], used: set[str]) -> str:
    """A fresh id from the suggestion's content (stable across a re-run from the cache)."""
    seed = json.dumps([kind, proposal], ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    chars = REVIEW_CONFIG["id_hash_chars"]
    for start in range(len(digest) - chars + 1):
        candidate = f"{REVIEW_CONFIG['id_prefix']}{digest[start:start + chars]}"
        if candidate not in used:
            return candidate
    raise ValueError(f"no free suggestion id for {kind} {proposal}")


def from_answer(raw: Any, review: ReviewInput, existing: Sequence[Mapping[str, Any]], *,
                raised_at: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The usable new suggestions of one model answer, and why the others were dropped.

    A suggestion is dropped when its proposal fails ``problems``, when it repeats one already
    raised (any status) or an earlier one of the same answer, or beyond
    ``wiki_review.max_suggestions``."""
    answers = raw.get("suggestions") if isinstance(raw, dict) else None
    if not isinstance(answers, list):
        raise ValueError(f"the review answer has no suggestions list: {str(raw)[:300]}")
    seen = raised_signatures(existing)
    used = {s["id"] for s in existing}
    fingerprints = review.page_fingerprints()
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for index, answer in enumerate(answers):
        kind = answer.get("kind") if isinstance(answer, dict) else None
        if kind not in KINDS:
            dropped.append(f"#{index}: unknown kind {kind!r}")
            continue
        title = _clean(answer.get("title"))
        proposal = proposal_from_answer(kind, answer)
        found = problems(kind, proposal, review)
        keys = signatures(kind, proposal)
        if found:
            dropped.append(f"#{index} {kind} {title!r}: {'; '.join(found)}")
        elif keys & seen:
            dropped.append(f"#{index} {kind} {title!r}: already raised")
        elif len(kept) >= REVIEW_CONFIG["max_suggestions"]:
            dropped.append(f"#{index} {kind} {title!r}: over max_suggestions")
        else:
            pages = [pid for pid in suggestion_pages(kind, proposal, _ids(answer.get("pages")))
                     if pid in review.pages]
            suggestion = {"id": new_id(kind, proposal, used), "kind": kind, "title": title,
                          "pages": pages, "reason": _clean(answer.get("reason")),
                          "proposal": proposal, "status": PENDING, "raised_at": raised_at,
                          "raised_on": {pid: fingerprints[pid] for pid in pages},
                          "page_marks": {}}
            used.add(suggestion["id"])
            seen |= keys
            kept.append(suggestion)
    return kept, dropped
