"""Reader-facing pages of step 9: no Material notice, disclaimer on every page, references toggle,
terms and about pages (built with ``mkdocs build --strict``)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from html import unescape
from pathlib import Path

import pytest
import yaml

from step_9_site.info_pages import about_body, config_value
from step_9_site.run import build_config
from step_6_plan import operations
from tests.site_fixtures import make_page, make_site_plan, run_site
from utils.config import PROJECT_ROOT, ROOT_CONFIG, section

SITE = section("site")
BODY = "## רקע\n\nטענה.[^g-00000001]\n\n[^g-00000001]: קבוצת טענות.\n"
APPROVED_BANNER = (
    "התוכן באתר זה נוצר באופן אוטומטי בידי בינה מלאכותית, על סמך שיחות בקבוצת ווטסאפ מרובת משתתפים. "
    "הוא משקף דעות, התרשמויות וחוויות אישיות של חברי הקבוצה, שאינן בהכרח מוסכמות על כולם; "
    "הוא לא נבדק ולא אומת, ועלול להיות שגוי, חלקי או לא מעודכן, לרבות בשל טעויות של הבינה המלאכותית. "
    "אין באמור ייעוץ רפואי, משפטי, כלכלי או מקצועי אחר, ואין להסתמך עליו כתחליף לייעוץ כזה או לבדיקה עצמאית. "
    "השימוש במידע הוא באחריות המשתמש בלבד."
)


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The generated site (strict build) with a merged page, a label page and a redirect stub."""
    tmp_path = tmp_path_factory.mktemp("site_pages")
    plan = operations.merge(make_site_plan(), "agencies", ["costs"]).plan
    pages = {
        "agencies": make_page(plan, "agencies", BODY, labels=("usa",)),
        "clinics": make_page(plan, "clinics", "טקסט.\n"),
    }
    shutil.copytree(
        PROJECT_ROOT / "docs" / "stylesheets", tmp_path / "docs" / "stylesheets"
    )
    run_site(tmp_path, plan, pages)
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "--strict",
            "-f",
            str(tmp_path / "mkdocs.yml"),
            "-d",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return tmp_path


def _html(site: Path, name: str) -> str:
    return (site / "out" / name).read_text(encoding="utf-8")


def _all_html(site: Path) -> dict[str, str]:
    return {
        path.relative_to(site / "out").as_posix(): path.read_text(encoding="utf-8")
        for path in (site / "out").rglob("*.html")
    }


def test_config_text_is_the_approved_banner() -> None:
    disclaimer = SITE["disclaimer"]
    assert disclaimer["text"] == APPROVED_BANNER
    assert disclaimer["label"] == "לתשומת לבך"  # the box's title: no colon
    assert disclaimer["terms_link_text"] == "תנאי השימוש המלאים ובקשות לתיקון או להסרה"
    assert SITE["contact_email"] == "4qu2ptpf9@mozmail.com"


def test_generated_config_turns_the_generator_notice_off_and_carries_the_page_settings() -> (
    None
):
    config = build_config(
        [], docs_dir=PROJECT_ROOT / "docs", mkdocs_path=PROJECT_ROOT / "mkdocs.yml"
    )
    extra = config["extra"]
    assert extra["generator"] is False
    assert extra["disclaimer"] == SITE["disclaimer"]
    assert extra["terms_path"] == "terms/"
    assert extra["refs_toggle"]["storage_key"] == SITE["refs_toggle"]["storage_key"]


def test_no_made_with_notice_anywhere(built_site: Path) -> None:
    for name, html in _all_html(built_site).items():
        assert "Made with" not in html, name
        assert 'class="md-copyright__highlight"' not in html, name
    # the about page credits the tool in its text, not as a footer notice
    assert "Material for MkDocs" in _html(built_site, "about/index.html")


def test_disclaimer_is_in_the_html_of_every_page(built_site: Path) -> None:
    pages = _all_html(built_site)
    assert {
        "index.html",
        "agencies/index.html",
        "labels/usa/index.html",
        "costs/index.html",
        "about/index.html",
        "terms/index.html",
        "404.html",
    } <= set(pages)
    for name, html in pages.items():
        if name.startswith("search/"):
            continue
        assert APPROVED_BANNER in html, name
        # Material's own info admonition, inside md-typeset (the theme styles it only there)
        box = re.search(
            r'<div class="wiki-disclaimer md-grid md-typeset">\s*<div class="admonition info" role="note">\s*'
            r'<p class="admonition-title">([^<]*)</p>\s*<p>(.*?)</p>\s*</div>',
            html,
            re.S,
        )
        assert box, name
        title, banner = box.groups()
        assert title == SITE["disclaimer"]["label"] and not title.endswith(":"), name
        assert APPROVED_BANNER in banner, name
        # the contact address is on the terms page only
        assert "mailto:" not in banner and SITE["contact_email"] not in banner, name
        terms_href = (
            r"\./" if name == "terms/index.html" else r"(?:\.\./|/)*terms/"
        )  # the terms page links to itself
        assert re.search(
            rf'<a href="{terms_href}">'
            + re.escape(SITE["disclaimer"]["terms_link_text"]),
            banner,
        ), name
        assert "wiki-disclaimer-bar" not in html and "<strong>" not in banner, name


def test_home_page_opens_with_one_plain_paragraph_ending_in_the_about_link(built_site: Path) -> None:
    html = _html(built_site, "index.html")
    article = html.split('<article class="md-content__inner md-typeset">', 1)[1]
    first = re.search(r"<p>(.*?)</p>", article, re.S).group(1)
    about = SITE["about"]
    intro = SITE["home"]["intro"].format(first="ינואר 2023", last="יוני 2024")
    assert first == f'{intro} <a href="about/">{about["home_link_text"]}</a>.'
    assert 'class="admonition' not in article.split("</article>", 1)[0]  # no box in the content


def test_terms_page_carries_the_contact_address_and_is_linked_from_banner_and_footer_not_in_nav(
    built_site: Path,
) -> None:
    html = _html(built_site, "terms/index.html")
    assert SITE["terms"]["title"] in html
    # Markdown writes a mail link as character references; the banner no longer carries one
    assert f'href="mailto:{SITE["contact_email"]}"' in unescape(html)
    assert SITE["terms"]["contact_placeholder"] not in html
    source = (PROJECT_ROOT / SITE["terms"]["source"]).read_text(encoding="utf-8")
    assert SITE["terms"]["contact_placeholder"] in source and SITE["contact_email"] not in source
    footer_link = re.compile(
        r'<a href="(?:\.\./|/)*terms/">' + re.escape(SITE["terms"]["footer_link_text"])
    )
    for name in ("index.html", "agencies/index.html", "404.html"):
        assert footer_link.search(_html(built_site, name)), name
    config = yaml.safe_load((built_site / "mkdocs.yml").read_text(encoding="utf-8"))
    assert (
        "terms.md" not in json.dumps(config["nav"])
        and "/terms.md" in config["not_in_nav"]
    )


def test_about_page_is_linked_only_from_the_home_page(built_site: Path) -> None:
    linking = [
        name
        for name, html in _all_html(built_site).items()
        if name != "about/index.html" and re.search(r'href="(?:\.\./)*about/"', html)
    ]
    assert linking == ["index.html"]
    config = yaml.safe_load((built_site / "mkdocs.yml").read_text(encoding="utf-8"))
    assert (
        "about.md" not in json.dumps(config["nav"])
        and "/about.md" in config["not_in_nav"]
    )
    search = json.loads(_html(built_site, "search/search_index.json"))
    assert not any(
        doc["location"].startswith(("about/", "terms/")) for doc in search["docs"]
    )


def test_about_page_names_the_models_of_config_and_the_two_links(
    built_site: Path,
) -> None:
    html = _html(built_site, "about/index.html")
    stages = ROOT_CONFIG["llm_client"]["stages"]
    for stage in ("extract", "plan", "write", "review", "apply", "propose"):
        assert f"<code>{stages[stage]['model']}</code>" in html
    for path in (
        "classifier.stages.knowledge.model",
        "classifier.stages.assign.model",
        "embedding.model",
        "entities.analyzer.model",
    ):
        assert f"<code>{config_value(path)}</code>" in html
    for link in SITE["about"]["links"]:
        assert f'href="{link["url"]}"' in html
    assert "https://github.com/GilMelnik/wiki-from-whatsapp" in html
    assert "https://github.com/GilMelnik/whatsapp-group-scraper" in html


def test_about_page_follows_a_model_change_in_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        ROOT_CONFIG["llm_client"]["stages"]["plan"], "model", "model-after-a-change"
    )
    body = about_body()
    assert body.count("`model-after-a-change`") == 1


def test_no_stylesheet_lets_a_block_s_first_letter_choose_its_direction() -> None:
    """A Hebrew paragraph or bullet that starts with a Latin word must stay right-to-left."""
    for sheet in ("docs/stylesheets/hebrew.css", "overrides/assets/wiki/wiki.css"):
        assert "plaintext" not in (PROJECT_ROOT / sheet).read_text(encoding="utf-8"), sheet


def test_about_page_states_its_principles_before_the_steps() -> None:
    about = SITE["about"]
    body = about_body()
    headings = [f"## {about[key]}" for key in ("principles_heading", "steps_heading", "links_heading")]
    assert [body.index(heading) for heading in headings] == sorted(body.index(heading) for heading in headings)
    assert all(f"- {principle}" in body for principle in about["principles"])
    # step 1 uses the embedder since the new splitter
    assert about["steps"][0]["models"] == ["embedding.model"]


def test_config_value_names_the_missing_part() -> None:
    with pytest.raises(KeyError, match="llm_client.stages.nothing.model"):
        config_value("llm_client.stages.nothing.model")


def test_wiki_pages_get_the_references_toggle_other_pages_do_not(
    built_site: Path,
) -> None:
    for name in ("agencies/index.html", "clinics/index.html"):
        html = _html(built_site, name)
        assert 'class="wiki-refs-toggle"' in html and 'aria-pressed="false"' in html, (
            name
        )
        assert SITE["refs_toggle"]["label"] in html
    for name in (
        "index.html",
        "labels/usa/index.html",
        "about/index.html",
        "terms/index.html",
        "404.html",
    ):
        assert "wiki-refs-toggle" not in _html(built_site, name).replace(
            "assets/wiki/refs-toggle.js", ""
        ), name


def test_toggle_assets_are_shipped_and_wired_with_the_configured_storage_key(
    built_site: Path,
) -> None:
    css = (built_site / "out" / "assets" / "wiki" / "wiki.css").read_text(
        encoding="utf-8"
    )
    script = (built_site / "out" / "assets" / "wiki" / "refs-toggle.js").read_text(
        encoding="utf-8"
    )
    # the selectors the references markup relies on (python-markdown footnotes)
    assert 'html:not(.wiki-refs-on) .md-typeset sup[id^="fnref"]' in css
    assert "html:not(.wiki-refs-on) .md-typeset .footnote" in css
    assert (
        "wiki-refs-on" in script
        and "document.currentScript.dataset.storageKey" in script
    )
    assert (
        SITE["refs_toggle"]["storage_key"] not in script
    )  # the key comes from config, not from the file
    for name in ("index.html", "agencies/index.html", "404.html"):
        html = _html(built_site, name)
        assert 'href="' in html and "assets/wiki/wiki.css" in html
        assert (
            f'refs-toggle.js" data-storage-key="{SITE["refs_toggle"]["storage_key"]}"'
            in html
        )
    # no page markdown carries the toggle: the footnotes stay plain markdown
    assert "wiki-refs" not in (built_site / "docs" / "agencies.md").read_text(
        encoding="utf-8"
    )


def test_the_new_pages_are_not_wiki_content(built_site: Path) -> None:
    index = json.loads(
        (built_site / "docs" / "wiki_index.json").read_text(encoding="utf-8")
    )
    assert {page["id"] for page in index["pages"]} == {"agencies", "clinics"}


def test_a_wiki_page_may_not_take_the_id_of_a_generated_page(tmp_path: Path) -> None:
    plan = operations.add_page(
        make_site_plan(),
        "about",
        parent_id="choosing",
        title_he="אודות",
        description="ת",
        scope="ה",
    ).plan
    with pytest.raises(ValueError, match="generated page 'about.md'"):
        run_site(tmp_path, plan, {"about": make_page(plan, "about", "x\n")})
