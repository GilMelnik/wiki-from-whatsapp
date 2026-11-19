"""Step 9's entity tag pages: next to the label pages, in the labels' navigation, in the agents'
pages and in the MCP index; only the entity's name and the pages, never anything private."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from step_6_plan.claim_records import SHORT_ID_HASH_CHARS, SHORT_ID_PREFIX, Record
from step_9_site.entity_tags import TAG, TAG_KIND, TAGS_CONFIG
from tests.claim_fixtures import claim_record
from tests.site_fixtures import build_html, make_page, make_site_plan, run_site
from utils.config import section
from utils.okf import parse_page

SITE = section("site")
MIN_CLAIMS = TAGS_CONFIG["min_claims"]
SOL, GEO = "entity_e0001", "entity_e0002"
HEADING = f"## {SITE['label_pages_heading']}"
SHORT_ID = re.compile(rf"{re.escape(SHORT_ID_PREFIX)}[0-9a-f]{{{SHORT_ID_HASH_CHARS}}}")


def _named(entity_id: str, name: str, start: int) -> list[Record]:
    return [claim_record(n, entity_ids=[entity_id], entity_names=[name], stance="positive")
            for n in range(start, start + MIN_CLAIMS)]


def _inputs() -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[Record]]:
    """Entity e0001 ("Clinica Sol") and e0002 (its name holds the plan's label "גאורגיה", so it
    waits for the owner), each named by enough claims cited on agencies and clinics."""
    plan = make_site_plan()
    sol, geo = _named("e0001", "Clinica Sol", 0), _named("e0002", "מרפאה בגאורגיה", 10)
    half = MIN_CLAIMS // 2
    cited = {"agencies": sol[:half] + geo[:half], "clinics": sol[half:] + geo[half:], "costs": []}
    pages = {}
    for page_id, records in cited.items():
        marks = "".join(f"[^{record['short_id']}]" for record in records)
        page = make_page(plan, page_id, f"## רקע\n\nטענה.{marks}\n", labels=("usa",))
        page["points"] = [{"kind": "k", "statement": "s", "claims": [r["short_id"] for r in records]}]
        pages[page_id] = page
    return plan, pages, [*sol, *geo]


def _read(docs: Path, rel: str) -> tuple[dict[str, Any], str]:
    text = (docs / rel).read_text(encoding="utf-8")
    _, block, body = text.split("---\n", 2)
    return yaml.safe_load(block), body.strip()


def test_a_tag_page_is_the_name_and_the_pages_in_the_labels_navigation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plan, pages, claims = _inputs()
    with caplog.at_level(logging.WARNING):
        result = run_site(tmp_path, plan, pages, claims)
    assert (result["labels"], result["entity_tags"], result["entity_tags_waiting"]) == (1, 1, 1)
    assert any("1 entities named often" in record.getMessage() for record in caplog.records)
    docs = tmp_path / "docs"
    meta, body = _read(docs, f"labels/{SOL}.md")
    assert meta == {"title": "Clinica Sol"}
    assert body.splitlines() == [
        HEADING,
        "",
        "- [כותרת agencies](../agencies.md): תיאור agencies",
        "- [כותרת clinics](../clinics.md): תיאור clinics",
    ]
    assert not (docs / "labels" / f"{GEO}.md").exists()
    nav = yaml.safe_load((tmp_path / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    [labels] = [entry[SITE["labels_nav_title"]] for entry in nav if SITE["labels_nav_title"] in entry]
    assert labels == [
        {SITE["label_kind_titles"]["country"]: [{"ארצות הברית": "labels/usa.md"}]},
        {SITE["label_kind_titles"][TAG_KIND]: [{"Clinica Sol": f"labels/{SOL}.md"}]},
    ]


def test_the_owners_decision_makes_the_waiting_entity_a_tag(tmp_path: Path) -> None:
    plan, pages, claims = _inputs()
    result = run_site(tmp_path, plan, pages, claims, decisions={"e0002": TAG})
    assert (result["entity_tags"], result["entity_tags_waiting"]) == (2, 0)
    assert _read(tmp_path / "docs", f"labels/{GEO}.md")[0] == {"title": "מרפאה בגאורגיה"}


def test_the_index_and_the_agents_pages_carry_the_tags_like_labels(tmp_path: Path) -> None:
    plan, pages, claims = _inputs()
    run_site(tmp_path, plan, pages, claims)
    docs = tmp_path / "docs"
    index = json.loads((docs / section("mcp")["index_file"]).read_text(encoding="utf-8"))
    assert list(index["labels"]) == [*plan["labels"], SOL]
    assert index["labels"][SOL] == {"title": "Clinica Sol", "description": "", "kind": TAG_KIND}
    assert {page["id"]: page["labels"] for page in index["pages"]} == {
        "agencies": ["usa", SOL], "costs": ["usa"], "clinics": ["usa", SOL]}
    agent = SITE["agent_markdown"]
    twin = (docs / agent["dir"] / "labels" / SOL / agent["twin_file"]).read_text(encoding="utf-8")
    assert twin.startswith("# Clinica Sol\n") and HEADING in twin and "כותרת clinics" in twin
    llms = (docs / agent["llms_file"]).read_text(encoding="utf-8")
    assert f"### {SITE['label_kind_titles'][TAG_KIND]}" in llms
    assert f"- [Clinica Sol]({SITE['url']}labels/{SOL}/{agent['twin_file']})\n" in llms


def test_the_page_front_matter_is_untouched(tmp_path: Path) -> None:
    plan, pages, claims = _inputs()
    run_site(tmp_path, plan, pages, claims)
    front_matter, _ = parse_page((tmp_path / "docs" / "agencies.md").read_text(encoding="utf-8"))
    assert front_matter == pages["agencies"]["front_matter"]


def test_nothing_private_or_from_the_claims_reaches_a_tag(tmp_path: Path) -> None:
    """The claim, thread and person ids of the records carry ``SECRET``; the strict mkdocs
    build must hold none of them, no claim text, and the tag pages no short id or count."""
    plan, pages, claims = _inputs()
    build_html(tmp_path, plan, pages, claims)
    written = [tmp_path / "mkdocs.yml", *(tmp_path / "docs").rglob("*"), *(tmp_path / "out").rglob("*")]
    texts = {path: path.read_bytes().decode("utf-8", errors="replace") for path in written if path.is_file()}
    assert (tmp_path / "out" / "labels" / SOL / "index.html").is_file()
    assert [path for path, text in texts.items() if "SECRET" in text or "Synthetic claim" in text] == []
    tag_files = {path: text for path, text in texts.items() if SOL in path.as_posix()}
    assert {path.name for path in tag_files} == {f"{SOL}.md", "index.md", "index.html"}  # page, twin, HTML
    for text in tag_files.values():
        assert not SHORT_ID.search(text)
    for path, text in tag_files.items():
        if path.suffix == ".md":
            assert str(MIN_CLAIMS) not in text
