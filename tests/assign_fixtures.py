"""Synthetic inputs for the assignment step: a plan, groups on disk and a fake classifier.

The plan and groups come from ``tests.plan_fixtures`` (pages agencies / costs / clinics /
hospital, labels usa / georgia; group ``i`` is "about topic i % 4"). The fake classifier
answers every question kind from small rules, so a test can say which page a group "is
about" by its topic number. Nothing here comes from real data.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from step_6_plan.assign import run
from step_6_plan.coverage import NONE
from tests.plan_fixtures import PAGE_IDS, FakeLLM, make_groups, make_plan
from utils.json_io import write_json_file
from utils.llm_client import Answer, Question
from utils.paths import Artifact

PageRule = Callable[[str, Sequence[str]], dict[str, float]]


def topic(state: str) -> int:
    return int(state.split("topic ")[1][0])


def by_topic(state: str, options: Sequence[str]) -> dict[str, float]:
    """Group of topic t -> page PAGE_IDS[t] with 0.9 (if that page is still an option)."""

    probs = {option: 0.0 for option in options}
    page = PAGE_IDS[topic(state)]
    probs[page if page in probs else NONE] = 0.9
    probs[NONE] = probs[NONE] or 0.1
    return probs


def usa_for_even(state: str, label_id: str) -> float:
    """Label usa applies to even topics; every other label never applies."""

    return 0.95 if label_id == "usa" and topic(state) % 2 == 0 else 0.05


class FakeClassifier:
    """Answers page (choice), label and screen (noul) questions; records every request."""

    provider = "fake"
    model = "fake-jev"

    def __init__(self, page: PageRule = by_topic,
                 label: Callable[[str, str], float] = usa_for_even,
                 screen: Callable[[str, str], float] = lambda state, page_id: 0.0) -> None:
        self.page, self.label, self.screen = page, label, screen
        self.items: list[tuple[str, Sequence[Question]]] = []
        self.tasks: list[str] = []

    def classify_many(self, items: Sequence[tuple[str, Sequence[Question]]], *, task: str = ""
                      ) -> list[list[Answer]]:
        self.items.extend(items)
        self.tasks.append(task)
        return [[self._answer(state, q) for q in questions] for state, questions in items]

    def _answer(self, state: str, question: Question) -> Answer:
        if question.kind == "choice":
            return Answer(question.id, self.page(state, question.options or ()))
        if question.id.startswith("label:"):
            return Answer(question.id, self.label(state, question.id.removeprefix("label:")))
        return Answer(question.id, self.screen(state, question.id.removeprefix("screen:")))

    def asked(self, kind: str) -> list[str]:
        """States that got a question of ``kind`` ("page", "label:<id>", "screen")."""
        return [state for state, questions in self.items
                if any(q.id == kind or (kind == "screen" and q.id.startswith("screen:"))
                       for q in questions)]


class Workspace:
    """Groups, plan and the assignments artifact in a temp folder."""

    def __init__(self, root: Path, group_count: int = 12) -> None:
        self.groups_path = root / "groups.json"
        self.plan_path = root / "plan.json"
        self.artifact = Artifact(root / "assignments.json", root / "assignments_edited.json")
        self.write_groups(make_groups(group_count))
        self.write_plan(make_plan())

    def write_groups(self, groups: list[dict[str, Any]]) -> None:
        write_json_file({"groups": groups, "metadata": {}}, self.groups_path)

    def groups(self) -> list[dict[str, Any]]:
        return json.loads(self.groups_path.read_text(encoding="utf-8"))["groups"]

    def write_plan(self, plan: dict[str, Any]) -> None:
        write_json_file(plan, self.plan_path)

    def plan(self) -> dict[str, Any]:
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def edited(self) -> dict[str, Any]:
        return json.loads(self.artifact.edited.read_text(encoding="utf-8"))

    def write_edited(self, payload: dict[str, Any]) -> None:
        write_json_file(payload, self.artifact.edited)

    def run(self, classifier: FakeClassifier | None = None, llm: FakeLLM | None = None,
            **kwargs: Any) -> dict[str, Any]:
        return run(aggregated_path=self.groups_path, plan_path=self.plan_path,
                   artifact=self.artifact, classifier=classifier or FakeClassifier(),
                   llm=llm or FakeLLM([]), **kwargs)
