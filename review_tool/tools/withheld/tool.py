"""The Withheld page of the review server: ``python -m review_tool --tool withheld``.

The wiki publishes a negative claim about a named provider, place or public body only when at
least two different members back it (``pages.withhold``). The same-complaint check
(``step_7_write_pages.same_complaint``) releases a lone one when a model found a second person
making the same complaint. Here the owner sees what the check released, before the pages are
written, and can keep any of those claims withheld; and searches the claims that stay withheld.
Nothing here calls a model, and no person is ever shown: only how many back a claim.
Endpoints, under ``/api/withheld``:

- ``GET  /summary``                   counts (withheld, released, still withheld, kept, groups),
  the lists per state of the check (answered, waiting, unusable), ``min_supporters``,
  ``answered_at`` (when the check last wrote its answers)
- ``GET  /released``                  ``{entities: [{entity_id, entity_name, released, groups:
  [{label, people, people_unmarked, claims: [claim + status]}]}]}``: the groups that release a
  claim, or would without the owner's marks; the most released first
- ``GET  /withheld?q=``               ``{items: [claim + why], total}``: what stays withheld
- ``POST /claims/{short_id}/keep``    ``{keep}`` -> ``{summary}``: set or take away the mark

A claim is ``{short_id, text, stance, supporter_count, month, entity_names, kept}``; ``status``
is released | kept | short | published, ``why`` is kept | waiting | unusable | short |
unmatched | alone. An unknown short id -> 404; marking a claim the rule does not withhold ->
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
from review_tool.tools.withheld.store import VetoTable
from step_7_write_pages.same_complaint_release import withheld_by_rule
from utils.paths import AUDIT_PATH

CLAIMS_KEY = "claims"
ENTITIES_KEY = "entities"
ANSWERS_KEY = "same_complaint"
VETOES_KEY = "withheld_vetoes"


class KeepRequest(BaseModel):
    keep: bool


class WithheldTool:
    NAME = "withheld"
    TITLE = "Withheld"
    READS = (CLAIMS_KEY, ENTITIES_KEY, ANSWERS_KEY)
    WRITES = (VETOES_KEY,)

    def __init__(self, graph: ArtifactGraph, *, audit: Path = AUDIT_PATH,
                 lock_timeout: float | None = None) -> None:
        vetoes = graph.node(VETOES_KEY).original
        self._vetoes: StoreHandle[VetoTable] = StoreHandle(
            partial(VetoTable, vetoes), (vetoes,), lock_timeout=lock_timeout
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
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def _view(self) -> WithheldView:
        return WithheldView(self._data.get(), frozenset(self._vetoes.get().kept))

    def summary(self) -> dict[str, Any]:
        return self._view().summary()

    def released(self) -> dict[str, Any]:
        return {"entities": self._view().released_entities()}

    def withheld(self, q: str = "") -> dict[str, Any]:
        items = self._view().still_withheld(q)
        return {"items": items, "total": len(items)}

    def keep(self, short_id: str, body: KeepRequest) -> dict[str, Any]:
        record = next((r for r in self._data.get().records if r["short_id"] == short_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"No claim {short_id} (any more); reload the page.")
        if not withheld_by_rule(record):
            raise HTTPException(
                status_code=409,
                detail="Two or more people back this claim (or it names nothing, or it is not "
                       "negative): the rule does not withhold it, so there is nothing to keep.",
            )
        with self._vetoes.mutate() as table:
            table.mark(short_id, body.keep)
        return {"summary": self.summary()}
