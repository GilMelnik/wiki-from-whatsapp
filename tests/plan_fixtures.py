"""Synthetic step-6 inputs: C6 groups, a valid flat proposal, and fake model / classifier.

The proposal is a 3-level tree: two top-level phases, one category under each, and pages
under the categories, plus two labels of kind "country". Nothing here comes from real data.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from step_6_plan.prompt import plan_from_proposal
from utils.llm_client import Answer, Question

PAGE_IDS = ("agencies", "costs", "clinics", "hospital")


def make_groups(count: int = 12) -> list[dict[str, Any]]:
    """``count`` C6 groups with synthetic English text and fields the prompt must not show."""

    return [
        {
            "group_id": f"g-{index:08x}",
            "claim_text": f"Synthetic claim number {index} about topic {index % 4}.",
            "representative_claim_id": f"t-SECRET{index}-abcd1234",
            "stance": ("positive", "negative", "neutral", "factual")[index % 4],
            "member_claim_ids": [f"t-SECRET{index}-abcd1234"],
            "entities": [f"e{index:04d}"],
            "entity_names": [f"Entity{index}"] if index % 2 else [],
            "supporter_count": index % 5,
            "opposer_count": index % 2,
            "first_date": "2023-01-05",
            "last_date": "2024-06-01" if index % 3 else "2023-01-20",
            "by_month": {"2023-01": {"supporters": 1, "opposers": 0}},
            "thread_count": 1 + index % 2,
            "contradicts": [],
        }
        for index in range(count)
    ]


def _page(page_id: str, parent: str, examples: Sequence[str]) -> dict[str, Any]:
    return {
        "id": page_id,
        "parent_id": parent,
        "title_he": f"כותרת {page_id}",
        "description": f"תיאור {page_id}",
        "scope": f"מה שייך לעמוד {page_id} ומה לא.",
        "aliases_he": [f"כינוי {page_id}"],
        "aliases_en": [page_id.replace("-", " ")],
        "example_group_ids": list(examples),
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
            _page("agencies", "choosing", ["g-00000000", "g-not-real"]),
            _page("costs", "choosing", ["g-00000001"]),
            _page("clinics", "medical", ["g-00000002"]),
            _page("hospital", "medical", []),
        ],
        "labels": [
            {"id": "usa", "kind": "country", "title_he": "ארצות הברית", "description": "ארה״ב"},
            {"id": "georgia", "kind": "country", "title_he": "גאורגיה", "description": "גאורגיה"},
        ],
    }


def make_plan() -> dict[str, Any]:
    """The valid C7 plan built from ``make_proposal``."""

    plan, errors = plan_from_proposal(make_proposal(), {g["group_id"] for g in make_groups()})
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
