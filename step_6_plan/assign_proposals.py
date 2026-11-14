"""Proposals for groups that fit no page (contract C8 ``proposals``).

The chat stage ``assign.proposals.stage`` (Sonnet 5.5) reads the plan (pages with their
scopes, categories, labels), the proposals already made (pending, accepted or rejected: never
to be made again) and the unplaced groups, one compact line each (``prompt.group_line``: id,
stance, support counts, months, entity names, claim text; nothing about people). It answers
with few, concrete proposals: a new page, a new label, or a wider scope for an existing page,
each naming the groups it would house. Nothing here changes the plan: the owner accepts a
proposal in the plan tool, which calls the plan operations (``add_page`` / ``add_label`` /
``edit``) and sets ``status: "accepted"``; the next assignment run then places the groups.

A stored proposal::

    {"id": "prop-<sha1[:8]>", "kind": "page|label|scope", "group_ids": [...], "title_he",
     "description", "reason", "status": "pending|accepted|rejected",
     "target_id": new page / label id suggested (page, label; "" if none was usable) or the
                  existing page to widen (scope),
     "parent_id": category for a new page (page; "" if none was usable),
     "label_kind": kind of a new label (label),
     "scope": the new page's scope, or the widened scope (page, scope)}
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from step_6_plan.assign_rules import ASSIGN_CONFIG
from step_6_plan.model import PLAN_CONFIG, iter_nodes, live_ids, retired_ids, slug_error
from step_6_plan.prompt import groups_block, proposal_from_plan
from utils.llm_client import LLMClient

PROPOSALS_CONFIG = ASSIGN_CONFIG["proposals"]
KINDS = ("page", "label", "scope")
PENDING = "pending"

PROPOSE_SYSTEM = f"""\
You help the owner curate a Hebrew wiki for an Israeli WhatsApp community of gay couples and \
gay men going through surrogacy abroad. The wiki's structure is a plan: categories, pages \
(each with a scope that says what belongs on it and what does not) and labels that cut across \
pages (for example by country). Claims from the chat were grouped, and a fast classifier put \
each claim group on a page by reading only the page titles and scopes. The user message lists \
the plan and the groups the classifier could NOT place on any page.

Your job: go through these groups, find the subjects they share, and for every subject the \
plan has no home for, propose the change that gives it one. Three kinds:
- "page": a new page, when several groups share a subject that no page covers and that a \
member would look up. Give target_id (a new lowercase Latin slug, letters, digits and single \
hyphens, at most {PLAN_CONFIG["slug_max_length"]} characters, not used by the plan), \
parent_id (the id of the category it belongs in), title_he, description (one Hebrew \
sentence) and scope (two or three Hebrew sentences: what belongs on it and what does not). \
The plan holds at most {PLAN_CONFIG["max_pages"]} pages.
- "label": a new label, when the groups share a facet value that cuts across pages and the \
labels lack it (for example a country). Give target_id (a new slug), label_kind (the kind, \
an existing one when it fits), title_he and description (one Hebrew sentence precise enough \
to decide yes or no for a single claim).
- "scope": a wider scope for an existing page, when the groups belong on that page but its \
scope does not say so. Give target_id (the existing page id), title_he (a short Hebrew name \
of what is added), description (one Hebrew sentence) and scope (the complete new scope text).

Rules:
- Few and concrete: at most {PROPOSALS_CONFIG["max_proposals"]} proposals, each backed by the \
groups it would house (group_ids, from the list only). A subject that two or more groups share \
is worth a proposal; a single group rarely is. Prefer "scope" over a new page when an \
existing page is the natural home. Groups that are too vague or off-topic, or that clearly fit \
a page as its scope is written (the classifier was only unsure), need no proposal.
- Every proposal is complete: a kind, its texts and its group_ids. Return an empty list when \
nothing is missing.
- Never repeat a proposal that is listed as already made (pending, accepted or rejected), in \
the same or other words.
- reason: one Hebrew sentence on why the plan needs it.
- Fields that do not apply to a kind are empty strings.
- Write Hebrew texts. {FORBIDDEN_TERM_INSTRUCTION}
- Never put a private person's name into a title, description or scope.
- Return only the JSON object asked for."""

_TEXT = {"type": "string"}
PROPOSALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "target_id": _TEXT, "parent_id": _TEXT, "label_kind": _TEXT,
            "title_he": _TEXT, "description": _TEXT, "scope": _TEXT, "reason": _TEXT,
            "group_ids": {"type": "array", "items": _TEXT},
        },
        "required": ["kind", "target_id", "parent_id", "label_kind", "title_he",
                     "description", "scope", "reason", "group_ids"],
        "additionalProperties": False,
    }}},
    "required": ["proposals"],
    "additionalProperties": False,
}

# The fields a stored proposal of each kind keeps, besides id, kind, group_ids and status.
KIND_FIELDS = {
    "page": ("title_he", "description", "scope", "reason", "target_id", "parent_id"),
    "label": ("title_he", "description", "reason", "target_id", "label_kind"),
    "scope": ("title_he", "description", "scope", "reason", "target_id"),
}


def _same_title(text: str) -> str:
    return " ".join(text.split()).casefold()


def proposed_group_ids(proposals: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every group already named by a proposal, whatever its status."""

    return {gid for proposal in proposals for gid in proposal.get("group_ids") or []}


def user_prompt(plan: Mapping[str, Any], groups: Sequence[Mapping[str, Any]],
                existing: Sequence[Mapping[str, Any]]) -> str:
    """The plan, the proposals already made and the unplaced groups."""

    flat = proposal_from_plan(dict(plan))
    for page in flat["pages"]:
        del page["example_group_ids"]
    made = [{key: proposal.get(key, "") for key in ("kind", "title_he", "description", "status")}
            for proposal in existing]
    return (
        f"<plan pages=\"{len(plan['pages'])}\">\n{json.dumps(flat, ensure_ascii=False)}\n</plan>\n\n"
        f"<proposals_already_made>\n{json.dumps(made, ensure_ascii=False)}\n"
        f"</proposals_already_made>\n\n{groups_block(groups)}\n\n"
        "Each group line: id | stance | support (+backers -disputers) | month or first..last "
        "month | threads | [entity names] | claim. Propose what the plan is missing for these "
        "unplaced groups."
    )


class Proposer:
    """Asks the chat model for proposals and keeps only the usable ones."""

    def __init__(self, llm: LLMClient, logger: logging.Logger) -> None:
        self.llm = llm
        self.logger = logger
        self.calls = 0

    def propose(self, plan: Mapping[str, Any], groups: Sequence[Mapping[str, Any]],
                existing: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """New pending proposals for ``groups`` (chunks of ``max_groups_per_call``)."""

        made: list[dict[str, Any]] = []
        size = PROPOSALS_CONFIG["max_groups_per_call"]
        for start in range(0, len(groups), size):
            chunk = groups[start:start + size]
            raw = self.llm.complete_json(
                PROPOSE_SYSTEM, user_prompt(plan, chunk, [*existing, *made]),
                task="assign-propose", response_schema=PROPOSALS_SCHEMA,
            )
            self.calls += 1
            made += self.clean(raw, plan, {g["group_id"] for g in chunk}, [*existing, *made])
        return made

    def clean(self, raw: Any, plan: Mapping[str, Any], group_ids: set[str],
              existing: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """The usable proposals of one answer, with ids, as pending proposals."""

        answers = raw.get("proposals") if isinstance(raw, dict) else None
        if not isinstance(answers, list):
            self.logger.warning(f"Proposal answer has no proposals list; ignored: {raw!r}")
            return []
        seen = {(p.get("kind"), _same_title(p.get("title_he", ""))) for p in existing}
        used_ids = {p.get("id") for p in existing}
        out: list[dict[str, Any]] = []
        for answer in answers[:PROPOSALS_CONFIG["max_proposals"]]:
            proposal = self._usable(answer, plan, group_ids)
            if proposal is None:
                continue
            key = (proposal["kind"], _same_title(proposal["title_he"]))
            if key in seen:
                self.logger.info(f"Proposal {proposal['title_he']!r} repeats one already made; dropped")
                continue
            seen.add(key)
            proposal = {"id": self._new_id(proposal, used_ids), **proposal, "status": PENDING}
            used_ids.add(proposal["id"])
            out.append(proposal)
        return out

    def _usable(self, answer: Any, plan: Mapping[str, Any], group_ids: set[str]
                ) -> dict[str, Any] | None:
        if not isinstance(answer, dict) or answer.get("kind") not in KINDS:
            self.logger.warning(f"Proposal of unknown kind dropped: {answer!r}")
            return None
        kind = answer["kind"]
        members = list(dict.fromkeys(g for g in answer.get("group_ids") or [] if g in group_ids))
        texts = {key: " ".join(str(answer.get(key) or "").split()) for key in KIND_FIELDS[kind]}
        missing = [key for key in ("title_he", "description", "scope")
                   if key in KIND_FIELDS[kind] and not texts[key]]
        if not members or missing:
            self.logger.warning(f"Proposal {texts['title_he']!r} dropped: groups {members}, "
                                f"missing texts {missing}")
            return None
        taken = live_ids(dict(plan)) | retired_ids(dict(plan))
        if kind == "scope":
            if texts["target_id"] not in plan["pages"]:
                self.logger.warning(f"Scope proposal for unknown page {texts['target_id']!r} dropped")
                return None
        elif slug_error(texts["target_id"]) or texts["target_id"] in taken:
            texts["target_id"] = ""
        categories = {node["id"] for node, _ in iter_nodes(plan["tree"])} - set(plan["pages"])
        if kind == "page" and texts["parent_id"] not in categories:
            texts["parent_id"] = ""
        if kind == "label" and slug_error(texts["label_kind"]):
            texts["label_kind"] = ""
        return {"kind": kind, "group_ids": members, **texts}

    @staticmethod
    def _new_id(proposal: Mapping[str, Any], used: set[Any]) -> str:
        seed = json.dumps([proposal["kind"], proposal["title_he"], sorted(proposal["group_ids"])],
                          ensure_ascii=False)
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
        chars = PROPOSALS_CONFIG["id_hash_chars"]
        for start in range(0, len(digest) - chars + 1):
            candidate = f"{PROPOSALS_CONFIG['id_prefix']}{digest[start:start + chars]}"
            if candidate not in used:
                return candidate
        raise ValueError(f"no free proposal id for {proposal['title_he']!r}")
