"""The claim-groups review tool of the review server: ``python -m review_tool --tool groups``.

The owner checks how step 5 grouped claims that say the same thing: each group with its
member claims, people counts and timeline; merges, splits, moves, the representative text and
contradiction links (both sides of a dispute); the groups the owner's rule keeps off the wiki
(``withheld``); and, above all, a ranked queue of merge suggestions (step 5 merges only what it
is nearly sure about). Endpoints, under ``/api/groups`` (view = ``multi, contradictions,
suggested, withheld, entity, stance, q, sort, order``; see ``views.GroupQuery``):

- ``GET  /summary``                          counters, entities, suggestion status, file read
- ``GET  /groups?<view>&offset&limit``       one page of the list
- ``GET  /groups/{id}?<view>``               the group in full and its place in the view
- ``GET  /suggestions?offset&limit``         one page of the ranked merge suggestions
- ``GET  /merge-check?group_ids=a&group_ids=b`` what merging them would do: the disputes
  between them (the merge stays allowed) and whether the result would be withheld
- ``POST /groups/merge`` ``{group_ids}``     into the oldest of them
- ``POST /groups/{id}/split`` ``{claim_ids}`` · ``/move`` ``{claim_id, target_id}`` ·
  ``/representative`` ``{claim_id|null}`` · ``/contradictions`` ``{group_id, linked}``
- ``POST /not-same`` ``{group_ids: [a, b]}`` (never suggested together again) ·
  ``DELETE /not-same/{record_id}`` (undo)

Every edit answers ``{group_id, group, summary, ...}`` (``group_id``: the group to show next).
Unknown group → 404; an edit that does not fit the current groups (or needs a step-5 run
first) → 409; a missing file → 409; the groups file locked by a step → 423. No endpoint calls
a paid API: vectors come from the embedding cache only. The plan and the assignments (steps 6
and 6b, both optional) are read only to say on which page a group is; they are watched by hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from review_tool.tools.groups.store import EditConflict, GroupNotFound, GroupsStore
from review_tool.tools.groups.suggestions import REVIEW_CONFIG
from review_tool.tools.groups.views import GroupQuery, SortKind, SortOrder
from utils.paths import AUDIT_PATH

GROUPS_KEY = "groups"
CLAIMS_KEY = "claims"
ENTITIES_KEY = "entities"
PLACEMENT_KEYS = ("plan", "assignments")
MAX_PAGE = int(REVIEW_CONFIG["max_page_size"])


def view_query(
    multi: bool = False,
    contradictions: bool = False,
    suggested: bool = False,
    withheld: bool = False,
    entity: str = "",
    stance: str = "",
    q: str = "",
    sort: SortKind = "size",
    order: SortOrder = "desc",
) -> GroupQuery:
    return GroupQuery(multi, contradictions, suggested, withheld, entity, stance, q, sort, order)


class MergeRequest(BaseModel):
    group_ids: list[str] = Field(min_length=2)


class SplitRequest(BaseModel):
    claim_ids: list[str] = Field(min_length=1)


class MoveRequest(BaseModel):
    claim_id: str
    target_id: str


class RepresentativeRequest(BaseModel):
    claim_id: str | None  # None = choose automatically again


class ContradictionRequest(BaseModel):
    group_id: str
    linked: bool


class NotSameRequest(BaseModel):
    group_ids: list[str] = Field(min_length=2, max_length=2)


class GroupsTool:
    NAME = "groups"
    TITLE = "Claim groups"
    READS = (GROUPS_KEY, CLAIMS_KEY, ENTITIES_KEY)
    WRITES = (GROUPS_KEY,)

    def __init__(
        self,
        graph: ArtifactGraph,
        *,
        audit_path: Path = AUDIT_PATH,
        lock_timeout: float | None = None,
    ) -> None:
        nodes = (graph.node(key) for key in (GROUPS_KEY, CLAIMS_KEY, ENTITIES_KEY))
        known = {node.key for node in graph.nodes}
        placement = {key: graph.node(key) for key in PLACEMENT_KEYS if key in known}
        # The private audit (who backed each claim) is not a graph node, and the plan and its
        # assignments are optional reads (no "missing input" warning): followed by hand.
        self._handle: StoreHandle[GroupsStore] = StoreHandle(
            partial(GroupsStore, *nodes, audit_path, **placement),
            (*graph.files(*self.READS, *placement), audit_path),
            lock_paths=graph.files(*self.WRITES),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        router.add_api_route("/summary", self.summary, methods=["GET"])
        router.add_api_route("/groups", self.groups, methods=["GET"])
        router.add_api_route("/groups/merge", self.merge, methods=["POST"])
        router.add_api_route("/groups/{group_id}", self.group, methods=["GET"])
        router.add_api_route("/groups/{group_id}/split", self.split, methods=["POST"])
        router.add_api_route("/groups/{group_id}/move", self.move, methods=["POST"])
        router.add_api_route(
            "/groups/{group_id}/representative", self.representative, methods=["POST"]
        )
        router.add_api_route(
            "/groups/{group_id}/contradictions", self.contradiction, methods=["POST"]
        )
        router.add_api_route("/suggestions", self.suggestions, methods=["GET"])
        router.add_api_route("/merge-check", self.merge_check, methods=["GET"])
        router.add_api_route("/not-same", self.not_same, methods=["POST"])
        router.add_api_route("/not-same/{record_id}", self.undo_not_same, methods=["DELETE"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    # ------------------------------------------------------------------ reads

    def summary(self) -> dict[str, Any]:
        return self._handle.get().views.summary()

    def groups(
        self,
        view: GroupQuery = Depends(view_query),
        limit: int = Query(ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return self._handle.get().views.page(view, offset, limit)

    def group(self, group_id: str, view: GroupQuery = Depends(view_query)) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().views.detail(group_id, view)

    def suggestions(
        self, limit: int = Query(ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0)
    ) -> dict[str, Any]:
        return self._handle.get().views.suggestions(offset, limit)

    def merge_check(self, group_ids: list[str] = Query(min_length=2)) -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().views.merge_check(group_ids)

    # ------------------------------------------------------------------ edits

    @contextmanager
    def _edit(self) -> Iterator[tuple[GroupsStore, dict[str, Any]]]:
        """The store under the lock, and the answer to fill; the summary is added after."""
        answer: dict[str, Any] = {}
        with _as_http_errors(), self._handle.mutate() as store:
            yield store, answer
            answer["summary"] = store.views.summary()

    def merge(self, body: MergeRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.merge(body.group_ids))
        return answer

    def split(self, group_id: str, body: SplitRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.split(group_id, body.claim_ids))
        return answer

    def move(self, group_id: str, body: MoveRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.move(group_id, body.claim_id, body.target_id))
        return answer

    def representative(self, group_id: str, body: RepresentativeRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.set_representative(group_id, body.claim_id))
        return answer

    def contradiction(self, group_id: str, body: ContradictionRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.set_contradiction(group_id, body.group_id, body.linked))
        return answer

    def not_same(self, body: NotSameRequest) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.mark_not_same(*body.group_ids))
        return answer

    def undo_not_same(self, record_id: int) -> dict[str, Any]:
        with self._edit() as (store, answer):
            answer.update(store.remove_not_same(record_id))
        return answer


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the UI shows as is."""
    try:
        yield
    except GroupNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except EditConflict as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc
