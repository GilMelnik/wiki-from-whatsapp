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

Errors: unknown thread, message or window → 404; an edit that does not fit, nothing to undo,
an old-format file → 409; a bad ``context`` range → 422; the file locked by a step → 423 (the
frame's write guard also answers 423 while step 1 runs).
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
from step_1_threads_split.reviewer.operations import EditRefused, UnknownItem
from step_1_threads_split.reviewer.store import OldFormat, ThreadsStore
from step_1_threads_split.reviewer.views import REVIEW_CONFIG, SortKind, parse_ranges

THREADS_KEY = "threads"
EXPORT_KEY = "export"


class MoveRequest(BaseModel):
    message_ids: list[str] = Field(min_length=1)
    target_id: str | None = None


class MergeRequest(BaseModel):
    thread_ids: list[str] = Field(min_length=2)


class DoneRequest(BaseModel):
    done: bool


class ThreadsTool:
    NAME = "threads"
    TITLE = "Conversations"
    READS = (THREADS_KEY, EXPORT_KEY)
    WRITES = (THREADS_KEY,)

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
            lock_paths=graph.files(*self.WRITES),
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
        self.spec = ToolSpec(
            name=self.NAME, title=self.TITLE, router=router, reads=self.READS, writes=self.WRITES
        )

    def summary(self) -> dict[str, Any]:
        return self._handle.get().summary()

    def rows(self, q: str = "", sort: SortKind = "time") -> dict[str, Any]:
        with _as_http_errors():
            return self._handle.get().views.rows(q, sort)

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


@contextmanager
def _as_http_errors() -> Iterator[None]:
    """Turn the store's own errors into HTTP errors whose detail the page shows as is."""
    try:
        yield
    except (UnknownItem, IndexError) as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except (EditRefused, OldFormat) as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc

