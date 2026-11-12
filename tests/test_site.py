"""Step 9 on synthetic inputs: nav, links, label pages, home page, redirects, cleanup, re-runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from step_6_plan import operations
from step_9_site.agent_pages import twin_docs_path, twin_url
from step_9_site.hebrew_search import prefix_variants
from step_9_site.run import run, sync_docs
from tests.site_fixtures import FIRST_MESSAGE, issues_of, make_page, make_site_plan, make_threads, run_site
from utils.paths import Artifact
from utils.threads_io import messages_period
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
    reference = f"[^g-00000001]: {section('site')['references']['start_mark']}g-00000001 · "
    assert reference in body and "קבוצה." not in body


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
    site = section("site")
    # one plain paragraph (no box): the groups' dates (fixture group g-00000001), then the about link
    intro = site["home"]["intro"].format(first="ינואר 2023", last="יוני 2024")
    assert body.split("\n\n")[0] == f"{intro} [{site['about']['home_link_text']}](about.md)."
    assert "!!!" not in body
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


def test_messages_period_is_the_earliest_and_the_latest_message_of_any_conversation() -> None:
    threads = make_threads("2025-05-05T10:00:00", "2022-10-08T20:49:00", "2026-08-31T23:59:00", "2024-01-01T00:00:00")
    assert messages_period(threads) == ("2022-10-08T20:49:00", "2026-08-31T23:59:00")
    with pytest.raises(ValueError, match="no message"):
        messages_period(make_threads())


def _home_sentence(first: str, last: str, about_href: str) -> str:
    site = section("site")
    intro = site["home"]["intro"].format(first=first, last=last)
    return f"{intro} [{site['about']['home_link_text']}]({about_href})."


def test_home_dates_are_those_of_the_first_and_the_last_message_not_of_the_claims(tmp_path: Path) -> None:
    """The groups (claims) all end in 2024; the chat runs on to 2026: the sentence follows the chat, in the
    markdown of the page and in its twin alike."""
    plan = make_site_plan()
    threads = make_threads("2026-08-31T23:59:00", "2022-10-08T20:49:00", "2025-01-02T10:00:00")
    run_site(tmp_path, plan, _pages(plan), threads=threads)
    docs = tmp_path / "docs"
    expected = _home_sentence("אוקטובר 2022", "אוגוסט 2026", "about.md")
    assert _read(docs, "index.md")[1].split("\n\n")[0] == expected
    twin = (docs / twin_docs_path("index.md")).read_text(encoding="utf-8")
    assert _home_sentence("אוקטובר 2022", "אוגוסט 2026", twin_url("about.md")) in twin.split("\n\n")


def test_home_dates_follow_the_edited_threads_file_when_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = make_site_plan()
    run_site(tmp_path, plan, _pages(plan))
    original, edited = tmp_path / "step_1" / "threads.json", tmp_path / "step_1" / "threads_edited.json"
    original.parent.mkdir()
    original.write_text(json.dumps(make_threads("2020-01-01T10:00:00", "2020-02-01T10:00:00")), encoding="utf-8")
    monkeypatch.setattr("utils.paths.THREADS", Artifact(original, edited))
    inputs = {name: tmp_path / file for name, file in
              {"plan_path": "plan.json", "pages_path": "wiki_pages.json", "groups_path": "claims_aggregated.json"}.items()}
    run(**inputs, docs_dir=tmp_path / "docs", mkdocs_path=tmp_path / "mkdocs.yml")
    assert _read(tmp_path / "docs", "index.md")[1].split("\n\n")[0] == _home_sentence("ינואר 2020", "פברואר 2020", "about.md")
    edited.write_text(json.dumps(make_threads("2021-03-01T10:00:00", "2021-04-01T10:00:00")), encoding="utf-8")
    run(**inputs, docs_dir=tmp_path / "docs", mkdocs_path=tmp_path / "mkdocs.yml")
    assert _read(tmp_path / "docs", "index.md")[1].split("\n\n")[0] == _home_sentence("מרץ 2021", "אפריל 2021", "about.md")


def test_a_missing_conversations_file_stops_step_9_and_names_step_1(tmp_path: Path) -> None:
    plan = make_site_plan()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    run_site(inputs, plan, _pages(plan))
    with pytest.raises(FileNotFoundError, match=r"conversations \(step 1\)"):
        run(plan_path=inputs / "plan.json", pages_path=inputs / "wiki_pages.json",
            groups_path=inputs / "claims_aggregated.json", threads_path=inputs / "absent.json",
            docs_dir=tmp_path / "docs", mkdocs_path=tmp_path / "mkdocs.yml")
    assert not (tmp_path / "docs").exists() and not (tmp_path / "mkdocs.yml").exists()


def test_a_moved_end_date_alone_rebuilds_the_home_page_and_its_twin(tmp_path: Path) -> None:
    """New messages and no new claim: nothing but the home page and its twin changes."""
    plan = make_site_plan()
    run_site(tmp_path, plan, _pages(plan))
    later = make_threads(FIRST_MESSAGE, "2026-08-31T10:00:00")
    written = run_site(tmp_path, plan, _pages(plan), threads=later)["written"]
    assert written == sorted(["index.md", twin_docs_path("index.md")])


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
    inputs = ("plan.json", "wiki_pages.json", "claims_aggregated.json", "threads.json")
    changed = [path for path in files if path.name not in inputs]
    assert all(path.stat().st_mtime_ns == stamps[path] for path in changed)


def test_changed_page_rewrites_only_what_depends_on_it(tmp_path: Path) -> None:
    plan = make_site_plan()
    run_site(tmp_path, plan, _pages(plan))
    pages = _pages(plan)
    pages["clinics"]["body_md"] = "## מרפאות\n\nטקסט חדש.\n"
    written = run_site(tmp_path, plan, pages)["written"]
    assert written == sorted([twin_docs_path("clinics.md"), "clinics.md", "wiki_index.json"])


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
