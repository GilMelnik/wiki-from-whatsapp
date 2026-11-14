"""Carrying out accepted suggestions in the background, one at a time, for the review tool.

A text correction (mistake, contradiction) is one paid model call that can take a minute or
two, so no apply runs inside a request: ``submit`` queues suggestions and returns at once,
one worker thread applies them in order through ``SuggestionsStore.apply`` (the store and
the applier decide everything), and every change of the runner's state is published as the
tool's event ``suggestions_apply`` (payload = ``state()``), so the page follows without a
reload. Outcomes, newest first:

- ``applied``: the store's result message;
- ``refused``: the store or the applier said no (``ApplyRefused`` / ``SuggestionConflict``
  / ``SuggestionNotFound``): their message and details, nothing written;
- ``failed``: anything else, e.g. the model provider refused the call: its message; nothing
  was written either (the applier writes only after the call) and the suggestion stays
  accepted.

While a suggestion is queued or being applied, the tool refuses to decide on it or edit it
(``busy``), so what is carried out is what the owner saw. Outcomes live in memory only; an
applied suggestion keeps its result in the suggestions file.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from review_tool.tools.suggestions.apply import ApplyRefused
from step_8_review.inputs import REVIEW_CONFIG
from step_8_review.proposals import ACCEPTED, STRUCTURAL
from review_tool.tools.suggestions.store import (
    SuggestionConflict,
    SuggestionNotFound,
    SuggestionsStore,
)
from utils.timestamps import utc_now_z

logger = logging.getLogger(__name__)
UI_CONFIG = REVIEW_CONFIG["ui"]
APPLY_EVENT = "suggestions_apply"
APPLIED, REFUSED, FAILED = "applied", "refused", "failed"


def is_paid(kind: str) -> bool:
    """A text correction is a model call; a plan change is not."""
    return kind not in STRUCTURAL


class ApplyRunner:
    """The queue of suggestions to carry out and the one thread that works through it."""

    def __init__(self, store: Callable[[], SuggestionsStore],
                 publish: Callable[[dict[str, Any]], None]) -> None:
        self._store = store
        self._publish = publish
        self._lock = threading.Lock()
        self._queue: deque[dict[str, Any]] = deque()
        self._running: dict[str, Any] | None = None
        self._outcomes: deque[dict[str, Any]] = deque(maxlen=UI_CONFIG["apply_outcomes_kept"])
        self._worker: threading.Thread | None = None
        self._working = False  # set and cleared under the lock: a submit never strands an entry

    # ------------------------------------------------------------------ queries

    def state(self) -> dict[str, Any]:
        """``{running, queue, outcomes}``: what is being applied, what waits, what came out."""
        with self._lock:
            return {"running": dict(self._running) if self._running else None,
                    "queue": [dict(entry) for entry in self._queue],
                    "outcomes": [dict(outcome) for outcome in self._outcomes]}

    def busy(self, suggestion_id: str) -> bool:
        """Whether the suggestion is queued or being applied."""
        with self._lock:
            return self._busy(suggestion_id)

    def _busy(self, suggestion_id: str) -> bool:
        running = self._running is not None and self._running["id"] == suggestion_id
        return running or any(entry["id"] == suggestion_id for entry in self._queue)

    # ------------------------------------------------------------------ changes

    def submit(self, suggestions: Iterable[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
        """Queue the accepted ones among ``suggestions`` (as the store lists them); returns
        the queued ids and why the others were not queued."""
        queued, refused = [], {}
        with self._lock:
            for suggestion in suggestions:
                sid = suggestion["id"]
                if self._busy(sid):
                    refused[sid] = UI_CONFIG["messages"]["already_queued"].format(id=sid)
                elif suggestion["status"] != ACCEPTED:
                    refused[sid] = REVIEW_CONFIG["messages"]["wrong_status"].format(
                        id=sid, status=suggestion["status_name"],
                        action=REVIEW_CONFIG["actions"]["apply"])
                else:
                    self._queue.append({"id": sid, "kind": suggestion["kind"],
                                        "title": suggestion.get("title", ""),
                                        "paid": is_paid(suggestion["kind"]), "queued_at": utc_now_z()})
                    queued.append(sid)
            if queued and not self._working:
                self._working = True
                self._worker = threading.Thread(target=self._work, name="suggestions-apply",
                                                daemon=True)
                self._worker.start()
        if queued:
            logger.info(f"Queued for applying: {queued}")
            self._announce()
        return queued, refused

    def withdraw(self, suggestion_ids: Iterable[str]) -> list[str]:
        """Take queued suggestions out of the queue (the one being applied runs to its end)."""
        wanted = set(suggestion_ids)
        with self._lock:
            removed = [entry["id"] for entry in self._queue if entry["id"] in wanted]
            self._queue = deque(entry for entry in self._queue if entry["id"] not in wanted)
        if removed:
            logger.info(f"Taken out of the apply queue: {removed}")
            self._announce()
        return removed

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the queue is worked through (tests, shutdown); False on timeout."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
            return not worker.is_alive()
        return True

    # ------------------------------------------------------------------ the worker

    def _announce(self) -> None:
        self._publish(self.state())

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._running = None
                    self._working = False
                    break
                self._running = {**self._queue.popleft(), "started_at": utc_now_z()}
                entry = dict(self._running)
            self._announce()
            outcome = self._apply(entry)
            with self._lock:
                self._outcomes.appendleft(outcome)
                self._running = None
            self._announce()

    def _apply(self, entry: dict[str, Any]) -> dict[str, Any]:
        base = {"id": entry["id"], "kind": entry["kind"], "title": entry["title"],
                "paid": entry["paid"], "started_at": entry["started_at"]}
        try:
            result = self._store().apply(entry["id"])["result"]
        except (ApplyRefused, SuggestionConflict) as exc:
            logger.warning(f"Suggestion {entry['id']} not applied: {exc} {exc.details}")
            return {**base, "status": REFUSED, "message": str(exc), "details": exc.details,
                    "ended_at": utc_now_z()}
        except SuggestionNotFound as exc:
            return {**base, "status": REFUSED, "message": str(exc), "details": [],
                    "ended_at": utc_now_z()}
        except Exception as exc:  # noqa: BLE001 - the worker reports every failure to the owner
            logger.exception(f"Applying suggestion {entry['id']} failed")
            return {**base, "status": FAILED,
                    "message": UI_CONFIG["messages"]["apply_failed"].format(error=exc),
                    "details": [repr(exc)], "ended_at": utc_now_z()}
        logger.info(f"Suggestion {entry['id']} applied: {result['message']}")
        return {**base, "status": APPLIED, "message": result["message"], "details": [],
                "result": result, "ended_at": utc_now_z()}
