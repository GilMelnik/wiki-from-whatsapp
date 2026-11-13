"""The owner's decisions on the review's suggestions (contract C12), as a library a thin HTTP
layer (task 36) can sit on.

Every operation reads the suggestions afresh (the EDITED file if present, else the step's
original) and writes the edited file only, after backing up the previous one; the step's
original keeps what the review raised. What may change: ``status`` (accept, reject, re-open),
``edited_proposal`` (the owner's correction; the same shape as ``proposal``, checked against
the current wiki before it is kept) and, once applied, ``applied_at`` / ``apply_result``.
Allowed moves: pending -> accepted | rejected; accepted -> pending | rejected | applied;
rejected -> pending; applied is final. Editing is open while a suggestion is pending or
accepted.

Applying (``step_8_review.apply``) changes the artifact the suggestion is about, then marks it
``applied``; a refused apply raises ``ApplyRefused`` and writes nothing. ``list`` adds live page
marks to open suggestions (a page changed or gone since the suggestion was raised) and display names
for kinds, statuses and marks (``wiki_review`` in config.json).
"""

from __future__ import annotations

import copy
import logging
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from step_8_review.apply import Applier
from step_8_review.inputs import (
    REVIEW_CONFIG,
    page_fingerprint,
    page_marks,
    read_json,
)
from step_8_review.proposals import (
    ACCEPTED,
    APPLIED,
    OPEN,
    PENDING,
    REJECTED,
    problems,
)
from utils.file_lock import file_lock
from utils.json_io import write_json_file
from utils.paths import PLAN, SUGGESTIONS, WIKI_PAGES, Artifact, resolve
from utils.reconcile import backup_file

logger = logging.getLogger(__name__)
MESSAGES = REVIEW_CONFIG["messages"]
ACTIONS = REVIEW_CONFIG["actions"]
# Which statuses each action starts from, and the status it leaves.
MOVES = {
    "accept": ((PENDING,), ACCEPTED),
    "reject": ((PENDING, ACCEPTED), REJECTED),
    "reopen": ((ACCEPTED, REJECTED), PENDING),
}
EDITABLE = (PENDING, ACCEPTED)


class SuggestionNotFound(Exception):
    """No suggestion has this id (404); ``args[0]`` is Hebrew."""


class SuggestionConflict(Exception):
    """The change does not fit the suggestion's status or the wiki (409); ``args[0]`` is
    Hebrew, ``details`` what code found."""

    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.details = details or []


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_fingerprints(pages_artifact: Artifact = WIKI_PAGES,
                         plan_artifact: Artifact = PLAN) -> dict[str, str]:
    """The fingerprint of every effective page that is still in the plan."""
    payload = read_json(pages_artifact.edited) or read_json(pages_artifact.original) or {}
    plan = read_json(resolve(plan_artifact)) or {"pages": {}}
    return {pid: page_fingerprint(page) for pid, page in (payload.get("pages") or {}).items()
            if pid in plan["pages"]}


class SuggestionsStore:
    """List, decide, edit and apply suggestions (see the module docstring)."""

    def __init__(self, artifact: Artifact = SUGGESTIONS, *, pages_artifact: Artifact = WIKI_PAGES,
                 plan_artifact: Artifact = PLAN, applier: Applier | None = None) -> None:
        self.artifact = artifact
        self.pages_artifact = pages_artifact
        self.plan_artifact = plan_artifact
        self.applier = applier or Applier(pages_artifact=pages_artifact, plan_artifact=plan_artifact)

    # ------------------------------------------------------------------ reads

    def _payload(self) -> dict[str, Any]:
        payload = read_json(self.artifact.edited) or read_json(self.artifact.original)
        if payload is None:
            raise FileNotFoundError(f"no review suggestions yet at {self.artifact.original}; "
                                    "run step 8 (python -m step_8_review.run)")
        return payload

    def list(self) -> dict[str, Any]:
        """Every suggestion in file order with its live page marks and Hebrew names, the
        counts per status and kind, and the review run's note."""
        payload = self._payload()
        current = current_fingerprints(self.pages_artifact, self.plan_artifact)
        items = []
        for suggestion in payload["suggestions"]:
            marks = (page_marks(suggestion.get("raised_on") or {}, current)
                     if suggestion["status"] in OPEN else {})
            items.append({
                **suggestion, "page_marks": marks,
                "kind_name": REVIEW_CONFIG["kind_names"][suggestion["kind"]],
                "status_name": REVIEW_CONFIG["status_names"][suggestion["status"]],
                "mark_names": {pid: REVIEW_CONFIG["page_marks"][mark] for pid, mark in marks.items()},
            })
        run = (payload.get("metadata") or {}).get("run") or {}
        return {"items": items,
                "counts": {"status": dict(Counter(s["status"] for s in items)),
                           "kind": dict(Counter(s["kind"] for s in items))},
                "note": run.get("note", ""), "generated_at": payload["metadata"].get("generated_at")}

    def get(self, suggestion_id: str) -> dict[str, Any]:
        """One suggestion as ``list`` shows it."""
        for item in self.list()["items"]:
            if item["id"] == suggestion_id:
                return item
        raise SuggestionNotFound(MESSAGES["not_found"].format(id=suggestion_id))

    @staticmethod
    def _find(payload: Mapping[str, Any], suggestion_id: str) -> dict[str, Any]:
        found = next((s for s in payload["suggestions"] if s["id"] == suggestion_id), None)
        if found is None:
            raise SuggestionNotFound(MESSAGES["not_found"].format(id=suggestion_id))
        return found

    # ------------------------------------------------------------------ writes

    def _change(self, suggestion_id: str, action: str, allowed: tuple[str, ...],
                **fields: Any) -> dict[str, Any]:
        """Set ``fields`` on one suggestion (after checking its status) and save. Read, check
        and write happen under one lock of the edited file, so two writers (the review tool's
        requests and its background applies) never lose each other's change."""
        with file_lock(self.artifact.edited):
            payload = copy.deepcopy(self._payload())
            suggestion = self._find(payload, suggestion_id)
            if suggestion["status"] not in allowed:
                raise SuggestionConflict(MESSAGES["wrong_status"].format(
                    id=suggestion_id, status=REVIEW_CONFIG["status_names"][suggestion["status"]],
                    action=ACTIONS[action]))
            for key, value in fields.items():
                if value is None:
                    suggestion.pop(key, None)
                else:
                    suggestion[key] = value
            backup_file(self.artifact.edited)
            write_json_file(payload, self.artifact.edited)
        logger.info(f"Suggestion {suggestion_id}: {action} -> {suggestion['status']}")
        return self.get(suggestion_id)

    def _move(self, suggestion_id: str, action: str) -> dict[str, Any]:
        allowed, status = MOVES[action]
        return self._change(suggestion_id, action, allowed, status=status)

    def accept(self, suggestion_id: str) -> dict[str, Any]:
        return self._move(suggestion_id, "accept")

    def reject(self, suggestion_id: str) -> dict[str, Any]:
        return self._move(suggestion_id, "reject")

    def reopen(self, suggestion_id: str) -> dict[str, Any]:
        return self._move(suggestion_id, "reopen")

    def edit(self, suggestion_id: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
        """Keep the owner's corrected proposal (checked against the current wiki first); a
        proposal equal to the raised one removes the correction."""
        suggestion = self._find(self._payload(), suggestion_id)
        review = self.applier.review()
        found = problems(suggestion["kind"], proposal, review)
        if found:
            raise SuggestionConflict(MESSAGES["bad_proposal"].format(errors="; ".join(found)), found)
        edited = None if dict(proposal) == suggestion["proposal"] else dict(proposal)
        return self._change(suggestion_id, "edit", EDITABLE, edited_proposal=edited)

    def apply(self, suggestion_id: str) -> dict[str, Any]:
        """Carry out an accepted suggestion and mark it applied; returns the suggestion and
        what changed. ``ApplyRefused`` when it cannot be carried out (nothing written)."""
        suggestion = self._find(self._payload(), suggestion_id)
        if suggestion["status"] != ACCEPTED:
            raise SuggestionConflict(MESSAGES["wrong_status"].format(
                id=suggestion_id, status=REVIEW_CONFIG["status_names"][suggestion["status"]],
                action=ACTIONS["apply"]))
        result = self.applier.apply(suggestion)
        logger.info(f"Applied {suggestion_id} ({suggestion['kind']}): {result.message}")
        applied = self._change(suggestion_id, "apply", (ACCEPTED,), status=APPLIED,
                               applied_at=_now(),
                               apply_result={"message": result.message, **result.details})
        return {"suggestion": applied, "result": {"message": result.message, **result.details}}
