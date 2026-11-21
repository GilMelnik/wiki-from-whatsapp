"""The Withheld page of the review server: ``python -m review_tool --tool withheld``.

The wiki publishes a negative claim about a named provider, place or public body only when at
least two different members back it (``withhold`` in ``config.json``). The same-complaint check
(``step_5_same_complaint.run``) releases a lone one when a model found a second person
making the same complaint. Here the owner sees what the check released, before the pages are
written, and can keep any of those claims withheld; goes through the claims that stay withheld
and releases any of them by hand (one decision per claim); and sees and undoes their own
releases. Nothing here calls a model, and no person is ever shown: only how many back a claim.
Endpoints, under ``/api/withheld``:

- ``GET  /summary``                   counts (withheld by the rule, released by the check,
  released by you, still withheld, kept, groups), the lists per state of the check (answered,
  waiting, unusable), ``min_supporters``, ``answered_at`` (when the check last wrote its answers)
- ``GET  /released``                  ``{entities: [{entity_id, entity_name, released, groups:
  [{label, people, people_unmarked, claims: [claim + status]}]}], by_you: [claim + status]}``:
  the groups that release a claim, or would without the owner's marks, the most released first;
  and the claims the owner released by hand
- ``GET  /withheld?q=``               ``{items: [claim + withheld + why + filed_under +
  filed_under_claims], total}``: every claim the rule withholds, each saying whether it is
  withheld now (``total`` counts those), so a claim the owner just released keeps its place in
  the page's list; ``filed_under`` is the most specific entity it names (the one the fewest
  claims name) and ``filed_under_claims`` how many claims name that entity
- ``POST /claims/{short_id}/keep``    ``{keep}`` -> ``{summary}``: set or take away the "keep
  withheld" mark (setting it takes a hand release away)
- ``POST /claims/{short_id}/release`` ``{release}`` -> ``{summary}``: release the claim by hand,
  or withhold it again (``WithheldView.release_marks``: the fewest marks that leave it published,
  or withheld; releasing a claim kept withheld that the check releases only takes the "keep
  withheld" mark away)

A claim is ``{short_id, thread_id, message_ids, text, stance, supporter_count, month,
entity_names, kept, hand_released}`` (``thread_id`` and ``message_ids``, the messages the claim
rests on, only open the conversation on this local site and mark them there); ``status`` is
released | kept | by_you | short | published, ``why`` is kept | waiting | unusable | short |
same_person | unmatched | alone (``short``: the owner's "keep withheld" marks left the claim's
group with too few people; ``same_person``: the check found the same complaint in another
claim, but one person made them all). An unknown short id -> 404; marking a claim the rule does not withhold ->
409; the marks file locked by another writer -> 423.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from review_tool.tools.withheld.overview import ClaimsData, WithheldView
from review_tool.tools.withheld.store import MarksTable
from step_5_same_complaint.withhold import withholds
from utils.paths import AUDIT_PATH

CLAIMS_KEY = "claims"
ENTITIES_KEY = "entities"
ANSWERS_KEY = "same_complaint"
MARKS_KEY = "withheld_vetoes"


class KeepRequest(BaseModel):
    keep: bool


class ReleaseRequest(BaseModel):
    release: bool


class WithheldTool:
    NAME = "withheld"
    TITLE = "Withheld"
    READS = (CLAIMS_KEY, ENTITIES_KEY, ANSWERS_KEY)
    WRITES = (MARKS_KEY,)

    def __init__(self, graph: ArtifactGraph, *, audit: Path = AUDIT_PATH,
                 lock_timeout: float | None = None) -> None:
        marks = graph.node(MARKS_KEY).original
        self._marks: StoreHandle[MarksTable] = StoreHandle(
            partial(MarksTable, marks), (marks,), lock_timeout=lock_timeout
        )
        self._data: StoreHandle[ClaimsData] = StoreHandle(
            partial(ClaimsData, graph.node(CLAIMS_KEY), graph.node(ENTITIES_KEY), audit,
                    graph.node(ANSWERS_KEY).original),
            (*graph.files(*self.READS), audit),
        )
        router = APIRouter()
        router.add_api_route("/summary", self.summary, methods=["GET"])
        router.add_api_route("/released", self.released, methods=["GET"])
        router.add_api_route("/withheld", self.withheld, methods=["GET"])
        router.add_api_route("/claims/{short_id}/keep", self.keep, methods=["POST"])
        router.add_api_route("/claims/{short_id}/release", self.release, methods=["POST"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def _view(self) -> WithheldView:
        return WithheldView(self._data.get(), self._marks.get().marks)

    def summary(self) -> dict[str, Any]:
        return self._view().summary()

    def released(self) -> dict[str, Any]:
        view = self._view()
        return {"entities": view.released_entities(), "by_you": view.by_you()}

    def withheld(self, q: str = "") -> dict[str, Any]:
        items = self._view().rule_withheld(q)
        return {"items": items, "total": sum(1 for item in items if item["withheld"])}

    def _require_withheld_by_rule(self, short_id: str, nothing_to: str) -> None:
        record = next((r for r in self._data.get().records if r["short_id"] == short_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No claim {short_id} (any more); reload the page.")
        if not withholds(record):
            raise HTTPException(
                status_code=409,
                detail="Two or more people back this claim (or it names nothing, or it is not "
                       f"negative): the rule does not withhold it, so there is nothing to {nothing_to}.",
            )

    def keep(self, short_id: str, body: KeepRequest) -> dict[str, Any]:
        self._require_withheld_by_rule(short_id, "keep")
        with self._marks.mutate() as table:
            table.keep(short_id, body.keep)
        return {"summary": self.summary()}

    def release(self, short_id: str, body: ReleaseRequest) -> dict[str, Any]:
        self._require_withheld_by_rule(short_id, "release")
        with self._marks.mutate() as table:
            table.replace(WithheldView(self._data.get(), table.marks).release_marks(short_id, body.release))
        return {"summary": self.summary()}
