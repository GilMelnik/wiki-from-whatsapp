"""The conversations tool of the review server: ``python -m review_tool --tool threads``.

The owner checks step 1's split of the chat into conversations and corrects it: which
messages belong together, where a conversation starts and ends. In gold-window mode the same
screens serve the hand-tagging of the gold windows. Endpoints, under ``/api/threads``:

- ``GET  /summary``           the file's state (``ready`` | ``old_format`` + ``problem``), the
  gold windows (``index, start, end, message_count, thread_ids, done``), the undo offer,
  ``quiet_minutes`` (step 1's pause rule) and ``context_step``
- ``GET  /threads?q&sort``     one row per thread (``sort`` = time | size; ``q`` searches the
  messages' text and senders)
- ``GET  /view?thread | window [&include …] [&context first-last …]``  the blocks of the
  thread view (a thread, its split parts, the conversations right around it) or of a gold
  window, plus whole threads to ``include`` and stretches of the chat to show as context
  (``step_1_threads_split.reviewer.views``)
- ``POST /windows/{index}/done``  ``{done}`` → the windows (``gold_progress.json``)
- ``POST /move``   ``{message_ids, target_id | null}`` (null: a new conversation)
- ``POST /merge``  ``{thread_ids}`` (the earliest keeps its id)
- ``POST /undo``   takes back the newest edit of this session
  Edits answer ``{label, focus, created?, removed?, summary}``.
- ``GET  /knowledge``              step 2's labels: ``{ready, reason, threshold, manual_count}``
- ``GET  /knowledge/{thread_id}``  ``{thread_id, label}``: step 2's answer for the conversation
  and the label in force (``label`` null when step 2 has none for it)
- ``POST /knowledge/{thread_id}``  ``{value: true | false | null}``: set by hand, or (null) back
  to step 2's answer; saved in step 2's edited file (``reviewer.knowledge``)

``GET /threads`` also takes ``knowledge`` = all | yes | no | manual (set by hand), and every row
carries its ``knowledge`` label.

Errors: unknown thread, message or window → 404; an edit that does not fit, nothing to undo,
an old-format file, a label before step 2 ran → 409; a bad ``context`` range → 422; the file
locked by a step → 423 (the frame's write guard also answers 423 while step 1 or step 2 runs).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from review_tool.artifacts import ArtifactGraph
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle
from step_1_threads_split.reviewer.gold_windows import GoldWindows
from step_1_threads_split.reviewer.history import EditHistory
from step_1_threads_split.reviewer.knowledge import KnowledgeFilter, KnowledgeLabels
from step_1_threads_split.reviewer.operations import EditRefused, UnknownItem
from step_1_threads_split.reviewer.store import OldFormat, ThreadsStore
from step_1_threads_split.reviewer.views import REVIEW_CONFIG, SortKind, parse_ranges

THREADS_KEY = "threads"
EXPORT_KEY = "export"
CLASSIFIED_KEY = "classified"


class MoveRequest(BaseModel):
    message_ids: list[str] = Field(min_length=1)
    target_id: str | None = None


class MergeRequest(BaseModel):
    thread_ids: list[str] = Field(min_length=2)


class DoneRequest(BaseModel):
    done: bool


class LabelRequest(BaseModel):
    value: bool | None


class ThreadsTool:
    NAME = "threads"
    TITLE = "Conversations"
    READS = (THREADS_KEY, EXPORT_KEY)
    # Step 2's file is optional here (the label control waits for it), so it is not a read the
    # frame reports as missing; writing it makes the write guard refuse labels while step 2 runs.
    WRITES = (THREADS_KEY, CLASSIFIED_KEY)

    def __init__(
        self,
        graph: ArtifactGraph,
        *,
        gold: GoldWindows | None = None,
        lock_timeout: float | None = None,
    ) -> None:
        threads = graph.node(THREADS_KEY)
        export = graph.node(EXPORT_KEY).original
        self.gold = GoldWindows.from_config() if gold is None else gold
        history = EditHistory(int(REVIEW_CONFIG["undo_depth"]))
        self._handle: StoreHandle[ThreadsStore] = StoreHandle(
            partial(ThreadsStore, threads.original, threads.edited, export, self.gold, history),
            graph.files(*self.READS),
            lock_paths=graph.files(THREADS_KEY),
            lock_timeout=lock_timeout,
        )
        classified = graph.node(CLASSIFIED_KEY)
        self._labels: StoreHandle[KnowledgeLabels] = StoreHandle(
            partial(KnowledgeLabels, classified.original, classified.edited),
            graph.files(CLASSIFIED_KEY),
            lock_timeout=lock_timeout,
        )
        router = APIRouter()
        router.add_api_route("/summary", self.summary, methods=["GET"])
        router.add_api_route("/threads", self.rows, methods=["GET"])
        router.add_api_route("/view", self.view, methods=["GET"])
        router.add_api_route("/windows/{index}/done", self.set_done, methods=["POST"])
        router.add_api_route("/move", self.move, methods=["POST"])
        router.add_api_route("/merge", self.merge, methods=["POST"])
        router.add_api_route("/undo", self.undo, methods=["POST"])
        router.add_api_route("/knowledge", self.knowledge, methods=["GET"])
        router.add_api_route("/knowledge/{thread_id}", self.label, methods=["GET"])
        router.add_api_route("/knowledge/{thread_id}", self.set_label, methods=["POST"])
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def summary(self) -> dict[str, Any]:
        return self._handle.get().summary()

    def rows(
        self, q: str = "", sort: SortKind = "time", knowledge: KnowledgeFilter = "all"
    ) -> dict[str, Any]:
        with _as_http_errors():
            answer = self._handle.get().views.rows(q, sort)
        labels = self._labels.get()
        items = [
            {**row, "knowledge": labels.label(row["thread_id"])}
            for row in answer["items"]
            if labels.shown(row["thread_id"], knowledge)
        ]
        return {**answer, "items": items, "knowledge": knowledge}

    def view(
        self,
        thread: str | None = None,
        window: int | None = Query(None, ge=0),
        include: list[str] = Query([]),
        context: list[str] = Query([]),
    ) -> dict[str, Any]:
        try:
            ranges = parse_ranges(context)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with _as_http_errors():
            views = self._handle.get().views
            if thread is not None:
                return views.thread_view(thread, include, ranges, window)
            if window is not None:
                return views.window_view(window, include, ranges)
        raise HTTPException(status_code=422, detail="Name a conversation (thread=…) or a gold window (window=…).")

    def set_done(self, index: int, body: DoneRequest) -> list[dict[str, Any]]:
        with _as_http_errors():
            self.gold.set_done(index, body.done)
            return self._handle.get().summary()["windows"]

    def move(self, body: MoveRequest) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.apply(store.editor().move(body.message_ids, body.target_id))

    def merge(self, body: MergeRequest) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.apply(store.editor().merge(body.thread_ids))

    def undo(self) -> dict[str, Any]:
        with _as_http_errors(), self._handle.mutate() as store:
            return store.undo()

    def knowledge(self) -> dict[str, Any]:
        return self._labels.get().state()

    def label(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "label": self._labels.get().label(thread_id)}

    def set_label(self, thread_id: str, body: LabelRequest) -> dict[str, Any]:
        with _as_http_errors(), self._labels.mutate() as labels:
            return {"thread_id": thread_id, "label": labels.set(thread_id, body.value)}


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the page shows as is."""
    try:
        yield
    except (UnknownItem, IndexError) as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except (EditRefused, OldFormat) as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc

