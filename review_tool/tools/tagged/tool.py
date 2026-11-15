"""The Tagged members page of the review server: ``python -m review_tool --tool tagged``.

Some providers are members of the group, and members recommend them by tagging them. Here the
owner says, once per tagged member, whether they are a provider and under which public name
step 3 shows their tag (``step_3_extract.tagged``); every other tag reaches the model as the
member's anonymous alias. It belongs to step 3's review and comes BEFORE the step: a run after a
change reads again the conversations whose tags now show differently. Nothing here calls a
model. Endpoints, under ``/api/tagged``:

- ``GET  /summary``                          counts per decision, the filter's ``min_taggers``,
  ``alias_example`` (how a tag of a member who is not a provider is shown)
- ``GET  /members?show=waiting|undecided|decided|all``   the members, the most tagged first
  (``waiting``, the default: undecided and tagged by ``min_taggers`` or more different members)
- ``GET  /members/{member}``                 the member, the tagging messages, their
  conversations and the suggested names
- ``POST /members/{member}/decision``        ``{provider, public_name?}`` -> ``{member, summary}``
- ``POST /members/{member}/clear``           -> ``{member, summary}``

A member the export does not tag -> 404; a provider without a name, or clearing no decision ->
409; the table locked by another writer -> 423.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from review_tool.tools.tagged.overview import SHOWS, WAITING, TaggedOverview
from review_tool.tools.tagged.store import DecisionConflict, TaggedTable
from step_3_extract.tagged import MIN_TAGGERS
from utils.threads_io import RENDER_CONFIG

EXPORT_KEY = "export"
THREADS_KEY = "threads"
CLAIMS_KEY = "claims"
TABLE_KEY = "tagged_members"


class DecisionRequest(BaseModel):
    provider: bool
    public_name: str | None = None


class TaggedTool:
    NAME = "tagged"
    TITLE = "Tagged members"
    READS = (EXPORT_KEY, THREADS_KEY, CLAIMS_KEY)
    WRITES = (TABLE_KEY,)

    def __init__(self, graph: ArtifactGraph, *, lock_timeout: float | None = None) -> None:
        table = graph.node(TABLE_KEY).original
        self._table: StoreHandle[TaggedTable] = StoreHandle(
            partial(TaggedTable, table), (table,), lock_timeout=lock_timeout
        )
        self._overview: StoreHandle[TaggedOverview] = StoreHandle(
            partial(
                TaggedOverview,
                graph.node(EXPORT_KEY).original,
                graph.node(THREADS_KEY),
                graph.node(CLAIMS_KEY),
            ),
            graph.files(*self.READS),
        )
        router = APIRouter()
        router.add_api_route("/summary", self.summary, methods=["GET"])
        router.add_api_route("/members", self.members, methods=["GET"])
        router.add_api_route("/members/{member}", self.member, methods=["GET"])
        router.add_api_route("/members/{member}/decision", self.decide, methods=["POST"])
        router.add_api_route("/members/{member}/clear", self.clear, methods=["POST"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def summary(self) -> dict[str, Any]:
        overview, table = self._overview.get(), self._table.get()
        return {
            "min_taggers": MIN_TAGGERS,
            # How step 3 shows the tag of a member who is not a provider ("משתתף N").
            "alias_example": RENDER_CONFIG["alias"].format(n="N"),
            "counts": overview.counts(table.members, MIN_TAGGERS),
            "export_available": overview.export_available,
            "table_exists": table.path.is_file(),
        }

    def members(self, show: str = Query(WAITING, pattern=f"^({'|'.join(SHOWS)})$")) -> dict[str, Any]:
        items = self._overview.get().rows(self._table.get().members, show, MIN_TAGGERS)
        return {"items": items, "total": len(items), "show": show}

    def member(self, member: str) -> dict[str, Any]:
        overview = self._known(member)
        return overview.detail(member, self._table.get().members.get(member), MIN_TAGGERS)

    def decide(self, member: str, body: DecisionRequest) -> dict[str, Any]:
        overview = self._known(member)
        with _as_http_errors(), self._table.mutate() as table:
            decision = table.decide(member, body.provider, body.public_name)
        return {"member": overview.detail(member, decision, MIN_TAGGERS), "summary": self.summary()}

    def clear(self, member: str) -> dict[str, Any]:
        overview = self._known(member)
        with _as_http_errors(), self._table.mutate() as table:
            table.clear(member)
        return {"member": overview.detail(member, None, MIN_TAGGERS), "summary": self.summary()}

    def _known(self, member: str) -> TaggedOverview:
        overview = self._overview.get()
        if member not in overview.members:
            raise HTTPException(
                status_code=404,
                detail="The chat export does not tag this member (any more); reload the list.",
            )
        return overview


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except DecisionConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
