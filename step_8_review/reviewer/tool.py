"""The suggestions tool of the review server: ``python -m review_tool --tool suggestions``.

The owner goes through what the whole-wiki review (step 8, contract C12) proposed: reads each
suggestion with its evidence, accepts, rejects or re-opens it (one or several at once),
corrects the proposal first when it is nearly right, has the accepted ones carried out and
sees what each changed. A thin layer over ``step_8_review.store.SuggestionsStore``: the store
and its applier decide every move, check and refusal; this module shows, collects and
reports. Endpoints, under ``/api/suggestions``:

- ``GET  /suggestions``            every suggestion (file order, live page marks, Hebrew
  names, ``paid``, ``busy``), the counts, the review's note and run, the apply queue;
  ``exists: false`` (and nothing else to show) before step 8 has run
- ``GET  /suggestions/{id}``       ``{suggestion, evidence, changes}`` (``evidence``: see
  ``reviewer.evidence``; ``changes``: what an applied suggestion changed, else ``null``)
- ``POST /suggestions/decide``     ``{ids, action: accept|reject|reopen}`` → ``{done,
  refused{id: message}}``; 409 when none could be done
- ``POST /suggestions/{id}/edit``  ``{proposal}`` → the suggestion (``store.edit``)
- ``GET  /applies``                the apply queue (``reviewer.applies.ApplyRunner.state``)
- ``POST /applies``                ``{ids}`` or ``{all: true}`` (every accepted one, file
  order) → 202 ``{queued, refused, applies}``; 409 when nothing could be queued
- ``POST /applies/withdraw``       ``{ids}`` → the queue without them

Unknown suggestion → 404; a move, an edit or an apply the store refuses, or a suggestion that
is queued or being applied → 409 with the store's Hebrew message (an invalid plan, which
keeps the evidence from loading, → 409 too); a step rewriting a file the
tool writes → 423 (the frame's write guard). The store reads its files afresh on every call
and locks the file it writes, and the applier writes the wiki's files through their own
guarded writes, so no ``StoreHandle.mutate`` is held here (it would lock those files a
second time and hold the tool for the length of a paid apply). The evidence reads the whole
wiki once and again only when one of its files changed (a ``StoreHandle`` for reading).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from review_tool import events
from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from step_6_plan.model import PlanError
from step_8_review.inputs import REVIEW_CONFIG, read_json
from step_8_review.proposals import ACCEPTED
from step_8_review.reviewer.applies import APPLY_EVENT, ApplyRunner, is_paid
from step_8_review.reviewer.changes import AppliedChanges
from step_8_review.reviewer.evidence import WikiView
from step_8_review.store import SuggestionConflict, SuggestionNotFound, SuggestionsStore
from utils.paths import BACKUPS_DIR

logger = logging.getLogger(__name__)
UI_CONFIG = REVIEW_CONFIG["ui"]
NO_COUNTS = {"status": {}, "kind": {}}


class Decision(BaseModel):
    ids: list[str] = Field(min_length=1)
    action: Literal["accept", "reject", "reopen"]


class ApplyRequest(BaseModel):
    ids: list[str] = []
    all: bool = False


class Withdrawal(BaseModel):
    ids: list[str] = Field(min_length=1)


class SuggestionsTool:
    NAME = "suggestions"
    TITLE = "Suggestions"
    READS = ("plan", "assignments", "pages", "groups")
    # The tool saves the suggestions; an apply writes the plan, the pages or the groups.
    WRITES = ("suggestions", "plan", "pages", "groups")

    def __init__(self, graph: ArtifactGraph, *,
                 store: Callable[[], SuggestionsStore] = SuggestionsStore,
                 publish: Callable[..., Any] = events.publish) -> None:
        self._make_store = store
        self._view: StoreHandle[WikiView] = StoreHandle(
            lambda: WikiView(self.store.applier.review()),
            graph.files("plan", "assignments", "pages", "groups"), lock_paths=())
        self.applies = ApplyRunner(
            lambda: self.store,
            lambda state: publish(APPLY_EVENT, tool=self.NAME, payload=state))
        router = APIRouter()
        router.add_api_route("/suggestions", self.overview, methods=["GET"])
        router.add_api_route("/suggestions/decide", self.decide, methods=["POST"])
        router.add_api_route("/suggestions/{suggestion_id}", self.detail, methods=["GET"])
        router.add_api_route("/suggestions/{suggestion_id}/edit", self.edit, methods=["POST"])
        router.add_api_route("/applies", self.apply_state, methods=["GET"])
        router.add_api_route("/applies", self.apply, methods=["POST"], status_code=202)
        router.add_api_route("/applies/withdraw", self.withdraw, methods=["POST"])
        self.spec = ToolSpec(name=self.NAME, title=self.TITLE, router=router,
                             reads=self.READS, writes=self.WRITES)

    @cached_property
    def store(self) -> SuggestionsStore:
        """Built on first use (it loads nothing itself: every call reads the files)."""
        return self._make_store()

    @cached_property
    def changes(self) -> AppliedChanges:
        return AppliedChanges(self.store.pages_artifact, BACKUPS_DIR)

    # ------------------------------------------------------------------ reads

    def overview(self) -> dict[str, Any]:
        applies = self.applies.state()
        if not self._exists():
            return {"exists": False, "items": [], "counts": NO_COUNTS, "note": "",
                    "generated_at": None, "run": None, "applies": applies,
                    "rerun_cost_usd": UI_CONFIG["rerun_cost_usd"]}
        listing = self.store.list()
        busy = {entry["id"] for entry in applies["queue"]}
        if applies["running"]:
            busy.add(applies["running"]["id"])
        metadata = self._metadata()
        return {
            **listing, "exists": True, "applies": applies,
            "items": [{**item, "paid": is_paid(item["kind"]), "busy": item["id"] in busy}
                      for item in listing["items"]],
            "run": {"model": metadata.get("model"), "input_tokens": metadata.get("input_tokens"),
                    "outdated": (metadata.get("run") or {}).get("outdated") or []},
            "rerun_cost_usd": UI_CONFIG["rerun_cost_usd"],
        }

    def detail(self, suggestion_id: str) -> dict[str, Any]:
        with _as_http_errors():
            suggestion = self.store.get(suggestion_id)
            view = self._view.get()
        return {"suggestion": {**suggestion, "paid": is_paid(suggestion["kind"]),
                               "busy": self.applies.busy(suggestion_id)},
                "evidence": view.evidence(suggestion),
                "changes": self.changes.of(suggestion, view)}

    def apply_state(self) -> dict[str, Any]:
        return self.applies.state()

    # ------------------------------------------------------------------ writes

    def decide(self, decision: Decision) -> dict[str, Any]:
        """Accept, reject or re-open each suggestion; the ones that cannot move are named."""
        self._known(decision.ids)
        move = getattr(self.store, decision.action)
        done, refused = [], {}
        for suggestion_id in dict.fromkeys(decision.ids):
            try:
                self._not_busy(suggestion_id)
                move(suggestion_id)
                done.append(suggestion_id)
            except SuggestionConflict as exc:
                refused[suggestion_id] = str(exc)
        if not done:
            raise HTTPException(status_code=409, detail="\n".join(refused.values()))
        return {"done": done, "refused": refused}

    def edit(self, suggestion_id: str,
             proposal: dict[str, Any] = Body(embed=True)) -> dict[str, Any]:
        with _as_http_errors():
            self._not_busy(suggestion_id)
            return self.store.edit(suggestion_id, proposal)

    def apply(self, request: ApplyRequest) -> dict[str, Any]:
        """Queue suggestions for applying (in the background, one at a time)."""
        if not request.ids and not request.all:
            raise HTTPException(status_code=422, detail=UI_CONFIG["messages"]["nothing_chosen"])
        items = self.store.list()["items"]
        if request.all:
            chosen = [item for item in items if item["status"] == ACCEPTED]
        else:
            self._known(request.ids, items)
            by_id = {item["id"]: item for item in items}
            chosen = [by_id[sid] for sid in dict.fromkeys(request.ids)]
        if not chosen:
            raise HTTPException(status_code=409, detail=UI_CONFIG["messages"]["none_accepted"])
        queued, refused = self.applies.submit(chosen)
        if not queued:
            raise HTTPException(status_code=409, detail="\n".join(refused.values()))
        return {"queued": queued, "refused": refused, "applies": self.applies.state()}

    def withdraw(self, request: Withdrawal) -> dict[str, Any]:
        self.applies.withdraw(request.ids)
        return self.applies.state()

    # ------------------------------------------------------------------ helpers

    def _exists(self) -> bool:
        artifact = self.store.artifact
        return artifact.edited.is_file() or artifact.original.is_file()

    def _metadata(self) -> dict[str, Any]:
        artifact = self.store.artifact
        payload = read_json(artifact.edited) or read_json(artifact.original) or {}
        return payload.get("metadata") or {}

    def _known(self, suggestion_ids: list[str], items: list[dict[str, Any]] | None = None) -> None:
        """404 for the first id that is not a suggestion."""
        known = {item["id"] for item in (items if items is not None else self.store.list()["items"])}
        for suggestion_id in suggestion_ids:
            if suggestion_id not in known:
                raise HTTPException(status_code=404, detail=REVIEW_CONFIG["messages"]["not_found"]
                                    .format(id=suggestion_id))

    def _not_busy(self, suggestion_id: str) -> None:
        if self.applies.busy(suggestion_id):
            raise SuggestionConflict(UI_CONFIG["messages"]["busy"].format(id=suggestion_id))


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """The store's own errors as HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except SuggestionNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except SuggestionConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
    except PlanError as exc:  # the wiki cannot be read as a whole (an invalid plan)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
