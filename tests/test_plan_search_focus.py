"""search_focus resolution for wiki planning."""

from __future__ import annotations

from step_6_plan.run import _normalize_plan, identity_plan
from utils.taxonomy import resolve_search_focus


def test_resolve_search_focus_prefers_taxonomy() -> None:
    assert resolve_search_focus("usa", ["usa"]) == "ארצות הברית gay surrogacy overview"
    assert (
        resolve_search_focus("merged-page", ["usa"], llm_value="ignored")
        == "ארצות הברית gay surrogacy overview"
    )


def test_resolve_search_focus_uses_llm_for_new_topics() -> None:
    assert resolve_search_focus("fundraising", ["fundraising"]) == ""
    assert (
        resolve_search_focus(
            "fundraising",
            ["fundraising"],
            llm_value="crowdfunding gay surrogacy",
        )
        == "crowdfunding gay surrogacy"
    )


def test_normalize_plan_uses_taxonomy_not_default() -> None:
    topics = {
        "usa": {
            "title": "ארצות הברית",
            "category": "geography",
            "claim_count": 3,
            "merged_claims": [],
        }
    }
    plan = _normalize_plan(
        {
            "pages": [
                {
                    "id": "usa",
                    "title": "ארצות הברית",
                    "category": "geography",
                    "source_tags": ["usa"],
                }
            ],
            "links": [],
        },
        topics,
    )
    assert plan["pages"][0]["search_focus"] == "ארצות הברית gay surrogacy overview"


def test_identity_plan_uses_taxonomy() -> None:
    topics = {
        "baby-gear": {
            "title": "ציוד",
            "category": "parenting",
            "claim_count": 2,
            "merged_claims": [],
        }
    }
    plan = identity_plan(topics)
    assert plan["pages"][0]["search_focus"] == "baby gear intended parents surrogacy"
