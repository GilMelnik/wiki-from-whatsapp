"""Step 11's references: short id · when · participants, rendered from the claim records (C6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from step_11_site.references import References, reference_text
from tests.claim_fixtures import claim_record, claim_records
from tests.site_fixtures import IDS, build_html, issues_of, make_page, make_site_plan, run_site
from utils.config import section
from utils.okf import parse_page

RLM = section("site")["references"]["start_mark"]


def _claim(**fields: Any) -> dict[str, Any]:
    return claim_record(1, **{"first_date": "2024-05-03", "last_date": "2024-05-20", "participant_count": 7,
                              **fields})


def test_a_reference_is_short_id_month_and_participants() -> None:
    short_id = _claim()["short_id"]
    assert reference_text(_claim()) == f"{RLM}{short_id} · מאי 2024 · 7 משתתפים בשיחה"
    assert reference_text(_claim(participant_count=1)) == f"{RLM}{short_id} · מאי 2024 · משתתף אחד בשיחה"


def test_a_range_of_months() -> None:
    claim = _claim(last_date="2025-06-01", participant_count=12)
    assert reference_text(claim) == f"{RLM}{claim['short_id']} · מאי 2024 – יוני 2025 · 12 משתתפים בשיחה"


def test_an_unknown_count_or_date_is_left_out() -> None:
    short_id = _claim()["short_id"]
    for unknown in (None, 0):
        assert reference_text(_claim(participant_count=unknown)) == f"{RLM}{short_id} · מאי 2024"
    assert reference_text(_claim(first_date=None, last_date=None)) == f"{RLM}{short_id} · 7 משתתפים בשיחה"


def test_unknown_marks_and_stored_definitions_are_dropped() -> None:
    refs = References([_claim(), claim_record(2, last_date="2024-05-05", participant_count=4)])
    body = (f"א.[^{IDS[1]}] ב.[^g-deadbeef] ג.[^{IDS[2]}][^{IDS[1]}]\n\n"
            f"[^{IDS[1]}]: טקסט ישן\n[^{IDS[2]}]: טקסט ישן\n")
    resolved = refs.resolve(body)
    assert resolved.body == f"א.[^{IDS[1]}] ב. ג.[^{IDS[2]}][^{IDS[1]}]\n"
    assert resolved.problems == (("unknown_reference", "g-deadbeef"),)
    rendered = refs.with_references(resolved.body)
    assert rendered.endswith(f"\n\n[^{IDS[1]}]: {RLM}{IDS[1]} · מאי 2024 · 7 משתתפים בשיחה\n"
                             f"[^{IDS[2]}]: {RLM}{IDS[2]} · מרץ 2023 – מאי 2024 · 4 משתתפים בשיחה\n")
    assert "טקסט ישן" not in rendered
    assert refs.with_references("טקסט בלי הפניות.\n") == "טקסט בלי הפניות.\n"


def test_the_site_renders_references_without_claim_text(tmp_path: Path) -> None:
    plan = make_site_plan()
    claims = claim_records(4)
    claims[1] = claim_record(1, participant_count=9, contradicts=claims[1]["contradicts"])
    stored = f"## רקע\n\nא.[^{IDS[2]}][^{IDS[1]}] ב.[^{IDS[2]}] ג.[^g-000000ff]\n"
    pages = {
        "agencies": make_page(plan, "agencies", stored),
        "costs": make_page(plan, "costs", f"## עלויות\n\nא.[^{IDS[0]}]\n\n[^{IDS[0]}]: ישן.\n"),
    }
    result = run_site(tmp_path, plan, pages, claims)
    docs = tmp_path / "docs"
    _, body = parse_page((docs / "agencies.md").read_text(encoding="utf-8"))
    article, references = body.split(f"\n\n[^{IDS[2]}]: ")
    assert article.endswith(f"א.[^{IDS[2]}][^{IDS[1]}] ב.[^{IDS[2]}] ג.")
    assert references.splitlines() == [
        f"{RLM}{IDS[2]} · מרץ 2023 – יוני 2024 · 5 משתתפים בשיחה",
        f"[^{IDS[1]}]: {RLM}{IDS[1]} · פברואר 2023 – יוני 2024 · 9 משתתפים בשיחה",
    ]
    _, costs = parse_page((docs / "costs.md").read_text(encoding="utf-8"))
    assert costs.endswith(f"[^{IDS[0]}]: {RLM}{IDS[0]} · ינואר 2023 · 3 משתתפים בשיחה\n") and "ישן" not in costs
    every_file = "".join(path.read_text(encoding="utf-8") for path in docs.rglob("*.md"))
    assert "Synthetic claim" not in every_file
    assert issues_of(result, "unknown_reference") == [("agencies", "g-000000ff")]
    index = json.loads((docs / "wiki_index.json").read_text(encoding="utf-8"))
    texts = {page["id"]: page["text"] for page in index["pages"]}
    assert texts["agencies"] == f"## רקע\n\nא.[^{IDS[2]}][^{IDS[1]}] ב.[^{IDS[2]}] ג.\n"
    assert texts["costs"] == f"## עלויות\n\nא.[^{IDS[0]}]\n"  # marks kept, no references


def test_no_private_id_reaches_the_built_site(tmp_path: Path) -> None:
    """Every claim, thread and person id of the test records carries ``SECRET``: none of them may
    reach what step 11 writes (docs, the agents' pages, the MCP index, mkdocs.yml) or the HTML."""
    plan = make_site_plan()
    cites = "".join(f"[^{short_id}]" for short_id in IDS[:4])
    pages = {page_id: make_page(plan, page_id, f"## רקע\n\nטענה.{cites}\n", related=("costs",))
             for page_id in ("agencies", "costs", "clinics")}
    build_html(tmp_path, plan, pages)
    written = [tmp_path / "mkdocs.yml", *(tmp_path / "docs").rglob("*"), *(tmp_path / "out").rglob("*")]
    texts = {path: path.read_bytes().decode("utf-8", errors="replace") for path in written if path.is_file()}
    assert any(f"{IDS[3]} · " in text for text in texts.values())  # the references are there
    assert [path for path, text in texts.items() if "SECRET" in text or "Synthetic claim" in text] == []
