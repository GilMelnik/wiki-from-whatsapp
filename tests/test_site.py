"""Step 9 on synthetic inputs: nav, links, label pages, home page, redirects, cleanup, re-runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from step_6_plan import operations
from step_9_site.hebrew_search import prefix_variants
from step_9_site.run import sync_docs
from tests.site_fixtures import issues_of, make_page, make_site_plan, run_site
from utils.config import PROJECT_ROOT, section
from utils.okf import parse_page

AGENCIES_BODY = (
    "## רקע\n\nראו [[costs|עלויות]], [[clinics]] ו-[[hospital|בית חולים]] וגם [[nowhere]].\n\n"
    'תגיות: [[label:usa|ארה"ב]] ו-[[label:georgia]]. קישור עצמי: [[agencies|כאן]].\n\n'
    "קוד: `[[costs]]`\n\n```\n[[clinics]]\n```\n\nמקור.[^g-00000001]\n\n[^g-00000001]: קבוצה.\n"
)


def _pages(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "agencies": make_page(plan, "agencies", AGENCIES_BODY, labels=("usa",), related=("costs", "hospital")),
        "costs": make_page(plan, "costs", "## עלויות\n\nחזרה ל[[agencies]].\n", status="draft"),
        "clinics": make_page(plan, "clinics", "## מרפאות\n\nטקסט.\n", labels=("usa",), summary="סיכום"),
    }


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    plan = make_site_plan()
    return tmp_path / "docs", run_site(tmp_path, plan, _pages(plan))


def _read(docs: Path, name: str) -> tuple[dict[str, Any], str]:
    return parse_page((docs / name).read_text(encoding="utf-8"))


def test_nav_follows_the_tree_and_skips_categories_without_published_pages(
    built: tuple[Path, dict[str, Any]],
) -> None:
    docs, _ = built
    nav = yaml.safe_load((docs.parent / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    assert nav == [
        {"בית": "index.md"},
        {"לפני": [{"בחירה": [{"כותרת agencies": "agencies.md"}, {"כותרת costs": "costs.md"}]}]},
        {"במהלך": [{"רפואי": [{"כותרת clinics": "clinics.md"}]}]},
        {"תגיות": [{"מדינות": [{"ארצות הברית": "labels/usa.md"}]}]},
    ]


def test_mkdocs_config_keeps_the_site_basics(built: tuple[Path, dict[str, Any]]) -> None:
    docs, _ = built
    config = yaml.safe_load((docs.parent / "mkdocs.yml").read_text(encoding="utf-8"))
    site = section("site")
    assert config["site_url"] == site["url"]
    assert config["docs_dir"] == "docs"
    assert config["theme"]["custom_dir"] == str(PROJECT_ROOT / site["custom_dir"])
    assert config["extra"]["coffee_url"] == site["coffee_url"]
    assert "footnotes" in config["markdown_extensions"]
    assert config["not_in_nav"] == "/about.md\n/terms.md"  # only the generated info pages


def test_missing_things_are_reported(built: tuple[Path, dict[str, Any]]) -> None:
    _, result = built
    assert issues_of(result, "page_not_written") == [("hospital", ""), ("later-page", "")]
    assert issues_of(result, "broken_page_link") == [("agencies", "hospital"), ("agencies", "nowhere")]
    assert issues_of(result, "broken_label_link") == [("agencies", "georgia")]
    assert issues_of(result, "label_not_carried") == [("", "georgia")]
    assert issues_of(result, "related_not_published") == [("agencies", "hospital")]
    assert result["issue_counts"]["broken_page_link"] == 2
    assert (result["published"], result["labels"], result["redirects"]) == (3, 1, 0)


def test_wiki_links_are_resolved_outside_code(built: tuple[Path, dict[str, Any]]) -> None:
    docs, _ = built
    front_matter, body = _read(docs, "agencies.md")
    assert "[עלויות](costs.md)" in body
    assert "[כותרת clinics](clinics.md)" in body  # no display text: the target's title
    assert "ו-בית חולים וגם nowhere." in body  # not published: plain text, no link
    assert '[ארה"ב](labels/usa.md)' in body
    assert "ו-גאורגיה." in body  # a label nobody carries has no page: its title, unlinked
    assert "[כאן](agencies.md)" in body
    assert "`[[costs]]`" in body and "```\n[[clinics]]\n```" in body
    # a body stored with its definition (older step 7): the stored text gives way to the reference
    assert "[^g-00000001]: g-00000001 · " in body and "קבוצה." not in body


def test_front_matter_round_trips_unchanged(built: tuple[Path, dict[str, Any]]) -> None:
    docs, _ = built
    plan = make_site_plan()
    for page_id, page in _pages(plan).items():
        front_matter, _ = _read(docs, f"{page_id}.md")
        assert front_matter == page["front_matter"]


def test_label_page_lists_the_pages_that_carry_it(built: tuple[Path, dict[str, Any]]) -> None:
    docs, _ = built
    meta, body = _read(docs, "labels/usa.md")
    assert meta == {"title": "ארצות הברית", "description": "ארה״ב"}
    assert body.splitlines()[-2:] == [
        "- [כותרת agencies](../agencies.md): תיאור agencies",
        "- [כותרת clinics](../clinics.md): תיאור clinics",
    ]
    assert not (docs / "labels" / "georgia.md").exists()


def test_home_page_presents_the_tree(built: tuple[Path, dict[str, Any]]) -> None:
    docs, _ = built
    meta, body = _read(docs, "index.md")
    assert meta["okf_version"] == section("pages")["okf_version"]
    assert meta["title"] == section("site")["mkdocs"]["site_name"]
    assert body.startswith(section("site")["home_intro"])
    lines = [line for line in body.splitlines() if line.startswith(("#", "-"))]
    assert lines == [
        "## לפני",
        "### בחירה",
        "- [כותרת agencies](agencies.md): תיאור agencies",
        "- [כותרת costs](costs.md): תיאור costs",
        "## במהלך",
        "### רפואי",
        "- [כותרת clinics](clinics.md): תיאור clinics",
    ]


def test_merged_ids_get_redirect_stubs_and_links_follow_them(tmp_path: Path) -> None:
    plan = operations.merge(make_site_plan(), "agencies", ["costs"]).plan
    plan = operations.merge(plan, "usa", ["georgia"]).plan
    pages = {
        "agencies": make_page(plan, "agencies", "ראו [[costs]] ו-[[label:georgia]].", labels=("georgia",)),
        "clinics": make_page(plan, "clinics", "טקסט.", related=("costs",)),
    }
    result = run_site(tmp_path, plan, pages)
    docs = tmp_path / "docs"
    _, body = _read(docs, "agencies.md")
    assert body == "ראו [כותרת agencies](agencies.md) ו-[ארצות הברית](labels/usa.md).\n"
    assert issues_of(result, "merged_page_link") == [("agencies", "costs")]
    assert issues_of(result, "merged_label_link") == [("agencies", "georgia")]
    meta, stub = _read(docs, "costs.md")
    assert meta == {"title": "כותרת agencies", "search": {"exclude": True}}
    assert '<meta http-equiv="refresh" content="0; url=../agencies/">' in stub
    assert "[כותרת agencies](agencies.md)" in stub
    _, label_stub = _read(docs, "labels/georgia.md")
    assert "url=../../labels/usa/" in label_stub and "[ארצות הברית](../labels/usa.md)" in label_stub
    config = yaml.safe_load((tmp_path / "mkdocs.yml").read_text(encoding="utf-8"))
    assert config["not_in_nav"] == "/costs.md\n/labels/georgia.md\n/about.md\n/terms.md"
    assert result["redirects"] == 2


def test_rerun_on_unchanged_inputs_writes_nothing(tmp_path: Path) -> None:
    plan = make_site_plan()
    first = run_site(tmp_path, plan, _pages(plan))
    assert "mkdocs.yml" in first["written"] and "wiki_index.json" in first["written"]
    files = [path for path in tmp_path.rglob("*") if path.is_file() and path.suffix in (".md", ".yml", ".json")]
    stamps = {path: path.stat().st_mtime_ns for path in files}
    second = run_site(tmp_path, plan, _pages(plan))
    assert (second["written"], second["removed"]) == ([], [])
    inputs = ("plan.json", "wiki_pages.json", "claims_aggregated.json")
    changed = [path for path in files if path.name not in inputs]
    assert all(path.stat().st_mtime_ns == stamps[path] for path in changed)


def test_changed_page_rewrites_only_what_depends_on_it(tmp_path: Path) -> None:
    plan = make_site_plan()
    run_site(tmp_path, plan, _pages(plan))
    pages = _pages(plan)
    pages["clinics"]["body_md"] = "## מרפאות\n\nטקסט חדש.\n"
    assert run_site(tmp_path, plan, pages)["written"] == ["clinics.md", "wiki_index.json"]


def test_files_the_step_did_not_write_are_removed_except_the_keep_list(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    for name in ("old-page.md", "sub/deep/x.md", "stylesheets/hebrew.css", "labels/gone.md"):
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text("x", encoding="utf-8")
    plan = make_site_plan()
    result = run_site(tmp_path, plan, _pages(plan))
    assert result["removed"] == ["labels/gone.md", "old-page.md", "sub/deep/x.md"]
    assert not (docs / "sub").exists()
    assert (docs / "stylesheets" / "hebrew.css").read_text(encoding="utf-8") == "x"
    assert (docs / "labels" / "usa.md").is_file()


def test_written_page_outside_the_plan_is_an_error_and_nothing_is_written(tmp_path: Path) -> None:
    plan = make_site_plan()
    pages = {**_pages(plan), "ghost": _pages(plan)["costs"]}
    with pytest.raises(ValueError, match="not in the plan.*ghost"):
        run_site(tmp_path, plan, pages)
    assert not (tmp_path / "docs").exists() and not (tmp_path / "mkdocs.yml").exists()


def test_invalid_front_matter_names_the_page(tmp_path: Path) -> None:
    plan = make_site_plan()
    pages = _pages(plan)
    del pages["costs"]["front_matter"]["claims"]
    with pytest.raises(ValueError, match="'costs'.*claims"):
        run_site(tmp_path, plan, pages)


def test_refuses_to_own_the_project_folder() -> None:
    with pytest.raises(ValueError, match="may own"):
        sync_docs(PROJECT_ROOT, {})


def test_label_kind_without_a_title_is_reported(tmp_path: Path) -> None:
    plan = operations.add_label(make_site_plan(), "x-label", title_he="תגית", description="ת", kind="odd").plan
    pages = _pages(plan)
    pages["costs"] = make_page(plan, "costs", "טקסט.", labels=("x-label",))
    result = run_site(tmp_path, plan, pages)
    assert issues_of(result, "untitled_label_kind") == [("", "odd")]
    nav = yaml.safe_load((tmp_path / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    assert {"odd": [{"תגית": "labels/x-label.md"}]} in nav[-1]["תגיות"]


def test_prefix_variants_strip_hebrew_prefixes() -> None:
    search = section("mcp")["search"]
    variants = prefix_variants("בקולומביה ומקסיקו, בית <p>והפונדקאית</p> קולומביה", search["hebrew_prefixes"], 3)
    assert {"מקסיקו", "פונדקאית", "הפונדקאית"} <= set(variants)
    assert "קולומביה" not in variants  # already a word of the text
    assert "ית" not in variants  # too short a stem
