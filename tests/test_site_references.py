"""Step 9's references: group id · when · participants, rendered from the groups (C6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from step_9_site.references import References, reference_text
from tests.plan_fixtures import make_groups
from tests.site_fixtures import issues_of, make_page, make_site_plan, run_site
from utils.config import section
from utils.okf import parse_page

RLM = section("site")["references"]["start_mark"]


def _group(**fields: Any) -> dict[str, Any]:
    group = {"group_id": "g-1a2b3c4d", "first_date": "2024-05-03", "last_date": "2024-05-20",
             "thread_count": 1, "participant_count": 7}
    return {**group, **fields}


def test_period_spans_the_dated_groups_and_follows_merges() -> None:
    references = References([
        _group(),
        _group(group_id="g-00000002", first_date="2023-02-01", last_date="2023-03-01", merged_from=["g-0000000f"]),
        _group(group_id="g-00000003", first_date=None, last_date=None),
    ])
    assert references.period(["g-1a2b3c4d", "g-0000000f", "g-00000003"]) == ("2023-02-01", "2024-05-20")
    assert references.period(["g-00000003", "g-99999999"]) is None
    assert references.period([]) is None


def test_a_reference_is_id_month_and_participants() -> None:
    assert reference_text(_group()) == f"{RLM}g-1a2b3c4d · מאי 2024 · 7 משתתפים בשיחה"
    assert reference_text(_group(participant_count=1)) == f"{RLM}g-1a2b3c4d · מאי 2024 · משתתף אחד בשיחה"


def test_a_range_of_months_and_several_conversations() -> None:
    group = _group(last_date="2025-06-01", thread_count=3, participant_count=12)
    assert reference_text(group) == f"{RLM}g-1a2b3c4d · מאי 2024 – יוני 2025 · 12 משתתפים ב־3 שיחות"


def test_an_unknown_count_shows_id_and_date_only() -> None:
    for unknown in ({"participant_count": None}, {"participant_count": 0}):
        assert reference_text(_group(thread_count=4, **unknown)) == f"{RLM}g-1a2b3c4d · מאי 2024"
    old = {k: v for k, v in _group().items() if k != "participant_count"}
    assert reference_text(old) == f"{RLM}g-1a2b3c4d · מאי 2024"


def test_marks_follow_merged_groups_and_unknown_marks_are_dropped() -> None:
    survivor = _group(merged_from=["g-0000aaaa", "g-0000bbbb"])
    refs = References([survivor, _group(group_id="g-00000002")])
    body = ("א.[^g-0000aaaa][^g-1a2b3c4d] ב.[^g-0000bbbb] ג.[^g-deadbeef][^g-00000002]\n\n"
            "[^g-0000aaaa]: טקסט ישן\n[^g-00000002]: טקסט ישן\n")
    resolved = refs.resolve(body)
    assert resolved.body == "א.[^g-1a2b3c4d] ב.[^g-1a2b3c4d] ג.[^g-00000002]\n"
    assert resolved.problems == (("merged_reference", "g-0000aaaa"), ("merged_reference", "g-0000bbbb"),
                                 ("unknown_reference", "g-deadbeef"))
    rendered = refs.with_references(resolved.body)
    assert rendered.endswith(f"\n\n[^g-1a2b3c4d]: {RLM}g-1a2b3c4d · מאי 2024 · 7 משתתפים בשיחה\n"
                             f"[^g-00000002]: {RLM}g-00000002 · מאי 2024 · 7 משתתפים בשיחה\n")
    assert "טקסט ישן" not in rendered
    assert refs.with_references("טקסט בלי הפניות.\n") == "טקסט בלי הפניות.\n"


def test_the_site_renders_references_without_claim_text(tmp_path: Path) -> None:
    plan = make_site_plan()
    groups = make_groups(4)
    groups[1]["participant_count"] = 9  # its claims come from two conversations
    groups[2]["participant_count"] = 3
    groups[2]["merged_from"] = ["g-000000aa"]
    stored = "## רקע\n\nא.[^g-00000002][^g-00000001] ב.[^g-000000aa] ג.[^g-000000ff]\n"
    pages = {
        "agencies": make_page(plan, "agencies", stored),
        "costs": make_page(plan, "costs", "## עלויות\n\nא.[^g-00000000]\n\n[^g-00000000]: ישן.\n"),
    }
    result = run_site(tmp_path, plan, pages, groups)
    docs = tmp_path / "docs"
    _, body = parse_page((docs / "agencies.md").read_text(encoding="utf-8"))
    article, references = body.split("\n\n[^g-00000002]: ")
    assert article.endswith("א.[^g-00000002][^g-00000001] ב.[^g-00000002] ג.")
    assert references.splitlines() == [
        f"{RLM}g-00000002 · ינואר 2023 – יוני 2024 · 3 משתתפים בשיחה",
        f"[^g-00000001]: {RLM}g-00000001 · ינואר 2023 – יוני 2024 · 9 משתתפים ב־2 שיחות",
    ]
    _, costs = parse_page((docs / "costs.md").read_text(encoding="utf-8"))
    assert costs.endswith(f"[^g-00000000]: {RLM}g-00000000 · ינואר 2023\n") and "ישן" not in costs
    every_file = "".join(path.read_text(encoding="utf-8") for path in docs.rglob("*.md"))
    assert "Synthetic claim" not in every_file
    assert issues_of(result, "merged_reference") == [("agencies", "g-000000aa")]
    assert issues_of(result, "unknown_reference") == [("agencies", "g-000000ff")]
    index = json.loads((docs / "wiki_index.json").read_text(encoding="utf-8"))
    texts = {page["id"]: page["text"] for page in index["pages"]}
    assert texts["agencies"] == "## רקע\n\nא.[^g-00000002][^g-00000001] ב.[^g-00000002] ג.\n"
    assert texts["costs"] == "## עלויות\n\nא.[^g-00000000]\n"  # marks kept, no references
