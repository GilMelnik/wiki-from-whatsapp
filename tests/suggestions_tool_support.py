"""The suggestions tool on step 8's synthetic workspace (``tests.review_fixtures``): one
suggestion of every kind, a fake page corrector, a fake group linker, the tool hosted in the
review app with a recording event sink. Nothing here comes from real data."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.jobs import JobRunner
from review_tool.tools.suggestions.apply_runner import APPLY_EVENT
from review_tool.tools.suggestions.tool import SuggestionsTool
from tests.pages_fixtures import FakeWriter
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, corrector, sentence_a, valid_answers
from tests.review_tool_support import BASE_URL, build_app

FIXED = "הקבוצה g-00000004 מספרת על הניסיון השני.[^g-00000004]"
WAIT_SECONDS = 10.0


def workspace_graph(ws: ReviewWorkspace) -> ArtifactGraph:
    """The artifact graph of the synthetic workspace (the tool watches these files)."""
    return ArtifactGraph([
        ArtifactNode("groups", "Groups", ws.groups_path, step="step_5"),
        ArtifactNode("plan", "Plan", ws.plan_artifact.original, ws.plan_artifact.edited,
                     "step_6", ("groups",)),
        ArtifactNode("assignments", "Assignments", ws.assignments_path, None, "step_6b",
                     ("groups", "plan")),
        ArtifactNode("pages", "Pages", ws.artifact.original, ws.artifact.edited, "step_7",
                     ("groups", "plan", "assignments")),
        ArtifactNode("suggestions", "Suggestions", ws.suggestions.original, ws.suggestions.edited,
                     "step_8", ("plan", "pages")),
    ])


class Gate:
    """A fake page corrector that waits until released (an apply that takes its time)."""

    def __init__(self, respond: Callable[[Any, Any, str], dict[str, Any]]) -> None:
        self.respond = respond
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, system: Any, user: Any, task: str) -> dict[str, Any]:
        self.started.set()
        assert self.release.wait(WAIT_SECONDS), "the test never released the apply"
        return self.respond(system, user, task)


def failing_provider(system: Any, user: Any, task: str) -> dict[str, Any]:
    raise RuntimeError("Error code: 400 - credit balance is too low")


class ToolSetup:
    """The workspace reviewed (6 pending suggestions), the tool and a client for it."""

    def __init__(self, root: Path, *, respond: Callable[[Any, Any, str], dict[str, Any]] | None = None,
                 review: bool = True,
                 jobs: Callable[[ArtifactGraph], JobRunner] | None = None) -> None:
        self.ws = ReviewWorkspace(root)
        if review:
            self.ws.review(FakeReviewer(valid_answers()))
        self.llm = FakeWriter("fake-apply",
                              respond=respond or corrector(sentence_a("g-00000004"), FIXED))
        self.events: list[dict[str, Any]] = []
        self.graph = workspace_graph(self.ws)
        self.tool = SuggestionsTool(self.graph, store=lambda: self.ws.store(self.llm),
                                    publish=self._record)
        self.runner = jobs(self.graph) if jobs else None
        app = build_app(root, specs=(self.tool.spec,), graph=self.graph, jobs=self.runner)
        self.client = TestClient(app, base_url=BASE_URL)

    def _record(self, event_type: str, **fields: Any) -> None:
        assert event_type == APPLY_EVENT and fields["tool"] == "suggestions"
        self.events.append(fields["payload"])

    # ------------------------------------------------------------------ requests

    def get(self, path: str, status: int = 200) -> Any:
        response = self.client.get(f"/api/suggestions{path}")
        assert response.status_code == status, response.text
        return response.json()

    def post(self, path: str, body: Any = None, status: int = 200) -> Any:
        response = self.client.post(f"/api/suggestions{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def ids(self) -> dict[str, str]:
        """Suggestion id by kind."""
        return {item["kind"]: item["id"] for item in self.get("/suggestions")["items"]}

    def item(self, suggestion_id: str) -> dict[str, Any]:
        return next(item for item in self.get("/suggestions")["items"] if item["id"] == suggestion_id)

    def decide(self, action: str, *ids: str, status: int = 200) -> Any:
        return self.post("/suggestions/decide", {"ids": list(ids), "action": action}, status)

    def apply(self, *ids: str, status: int = 202) -> Any:
        return self.post("/applies", {"ids": list(ids)}, status)

    def wait(self) -> dict[str, Any]:
        """Let the background applies finish; returns the queue's state."""
        assert self.tool.applies.wait(WAIT_SECONDS), "the applies did not finish"
        return self.get("/applies")
