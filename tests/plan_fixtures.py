"""Synthetic step-6 inputs: a valid flat proposal over ``tests.claim_fixtures.claim_records``,
and fake model / classifier.

The proposal is a 3-level tree: two top-level phases, one category under each, and pages
under the categories, plus two labels of kind "country". Its example claims are the short ids
of claim records 0, 1 and 2 (and one id no record has). Nothing here comes from real data.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from step_6_plan.prompt import plan_from_proposal
from tests.claim_fixtures import claim_record, claim_records
from utils.llm_client import Answer, Question

PAGE_IDS = ("agencies", "costs", "clinics", "hospital")


def short_id_of(n: int) -> str:
    """The short id of ``claim_record(n)``."""

    return claim_record(n)["short_id"]


def _page(page_id: str, parent: str, examples: Sequence[str]) -> dict[str, Any]:
    return {
        "id": page_id,
        "parent_id": parent,
        "title_he": f"כותרת {page_id}",
        "description": f"תיאור {page_id}",
        "scope": f"מה שייך לעמוד {page_id} ומה לא.",
        "aliases_he": [f"כינוי {page_id}"],
        "aliases_en": [page_id.replace("-", " ")],
        "example_claims": list(examples),
    }


def make_proposal() -> dict[str, Any]:
    """A valid answer in the model's flat format."""

    return {
        "top_level_rationale": "Phases of the process.",
        "nodes": [
            {"id": "before", "parent_id": None, "title_he": "לפני", "description": "שלב א"},
            {"id": "during", "parent_id": None, "title_he": "במהלך", "description": "שלב ב"},
            {"id": "choosing", "parent_id": "before", "title_he": "בחירה", "description": "ק"},
            {"id": "medical", "parent_id": "during", "title_he": "רפואי", "description": "ק"},
        ],
        "pages": [
            _page("agencies", "choosing", [short_id_of(0), "g-not-real"]),
            _page("costs", "choosing", [short_id_of(1)]),
            _page("clinics", "medical", [short_id_of(2)]),
            _page("hospital", "medical", []),
        ],
        "labels": [
            {"id": "usa", "kind": "country", "title_he": "ארצות הברית", "description": "ארה״ב"},
            {"id": "georgia", "kind": "country", "title_he": "גאורגיה", "description": "גאורגיה"},
        ],
    }


def make_plan() -> dict[str, Any]:
    """The valid C7 plan built from ``make_proposal``."""

    plan, errors = plan_from_proposal(make_proposal(), {c["short_id"] for c in claim_records()})
    assert errors == []
    return plan


class FakeLLM:
    """Returns the queued answers in order and records every call."""

    model = "fake-opus"

    def __init__(self, answers: Sequence[dict[str, Any]]) -> None:
        self.answers = [copy.deepcopy(a) for a in answers]
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, system: Any, user: Any, task: str = "", response_schema: dict | None = None
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "task": task, "schema": response_schema})
        return self.answers.pop(0)


class FakeClassifier:
    """Answers every page question with the probabilities ``pick(state)`` returns."""

    def __init__(self, pick: Any) -> None:
        self.pick = pick
        self.items: list[tuple[str, Sequence[Question]]] = []

    def classify_many(
        self, items: Sequence[tuple[str, Sequence[Question]]], *, task: str = ""
    ) -> list[list[Answer]]:
        self.items.extend(items)
        return [[Answer(questions[0].id, self.pick(state))] for state, questions in items]
