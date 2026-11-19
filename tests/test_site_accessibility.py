"""Accessibility of the built site (WCAG 2.0 AA, Israeli Standard 5568): the contrast of our own
colours, the accessibility button and its menu in the header of every page, the statement page
and its links, no request to another host (built with ``mkdocs build --strict``)."""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest
import yaml

from step_6_plan import operations
from tests.site_fixtures import IDS, build_html, make_page, make_site_plan
from utils.config import PROJECT_ROOT, section

SITE = section("site")
A11Y = SITE["accessibility"]
MENU = A11Y["menu"]
ASSETS = PROJECT_ROOT / SITE["custom_dir"] / "assets" / "wiki"
CSS = (ASSETS / "a11y.css").read_text(encoding="utf-8")
WHITE, BLACK = "#ffffff", "#000000"
SLATE_BACKGROUND = "#1e2129"  # Material's dark page: hsla(232, 15%, 14%)
TEXT_RATIO = 4.5
BODY = (
    f"## רקע\n\nטענה.[^{IDS[1]}] ראו [[clinics|המרפאות]].\n\n"
    "| עמודה | עמודה שנייה |\n| --- | --- |\n| א | ב |\n"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A strict build: home, a wiki page with references and a table, a label page, a stub, the info pages."""
    tmp_path = tmp_path_factory.mktemp("site_accessibility")
    plan = operations.merge(make_site_plan(), "agencies", ["costs"]).plan
    pages = {
        "agencies": make_page(plan, "agencies", BODY, labels=("usa",)),
        "clinics": make_page(plan, "clinics", "טקסט.\n"),
    }
    build_html(tmp_path, plan, pages)
    return tmp_path


def _pages(built: Path) -> dict[str, str]:
    """Every HTML page of the build (the search's own files left out)."""
    out = built / "out"
    return {
        path.relative_to(out).as_posix(): path.read_text(encoding="utf-8")
        for path in out.rglob("*.html")
        if not path.relative_to(out).as_posix().startswith("search/")
    }


def _luminance(colour: str) -> float:
    channels = [int(colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    """The WCAG 2.0 contrast ratio of two opaque ``#rrggbb`` colours."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _properties(selector: str) -> dict[str, str]:
    """The custom properties a11y.css sets in the rule for exactly ``selector``."""
    match = re.search(r"(?:^|\n)" + re.escape(selector) + r" \{([^}]*)\}", CSS)
    assert match, selector
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", match.group(1)))


def test_contrast_is_computed_as_wcag_says() -> None:
    assert contrast(WHITE, BLACK) == pytest.approx(21)
    assert contrast("#777777", WHITE) == pytest.approx(4.48, abs=0.01)


def test_the_palette_takes_our_colours_and_no_web_font() -> None:
    theme = SITE["mkdocs"]["theme"]
    assert [(scheme["primary"], scheme["accent"]) for scheme in theme["palette"]] == [("custom", "custom")] * 2
    assert theme["font"] is False  # Material would load Roboto from Google's font service


def test_our_text_colours_reach_4_5_to_1_in_both_schemes() -> None:
    primary = _properties('[data-md-color-primary="custom"]')
    accent = _properties('[data-md-color-accent="custom"]')
    dark_links = _properties('[data-md-color-scheme="slate"][data-md-color-primary="custom"]')
    dark_accent = _properties('[data-md-color-scheme="slate"][data-md-color-accent="custom"]')
    pairs = {
        "header text": (primary["--md-primary-bg-color"], primary["--md-primary-fg-color"]),
        "links": (primary["--md-typeset-a-color"], WHITE),
        "hovered links": (accent["--md-accent-fg-color"], WHITE),
        "dark links": (dark_links["--md-typeset-a-color"], SLATE_BACKGROUND),
        "dark hovered links": (dark_accent["--md-accent-fg-color"], SLATE_BACKGROUND),
        # dark scheme, the header keeps the primary colour
        "dark header text": (primary["--md-primary-bg-color"], primary["--md-primary-fg-color"]),
    }
    for name, (text, background) in pairs.items():
        assert contrast(text, background) >= TEXT_RATIO, name


def test_high_contrast_reaches_7_to_1() -> None:
    light = _properties("html.wiki-a11y-contrast [data-md-color-scheme]")
    dark = {**light, **_properties('html.wiki-a11y-contrast [data-md-color-scheme="slate"]')}
    for scheme in (light, dark):
        background = scheme["--md-default-bg-color"]
        for key in ("--md-default-fg-color", "--md-typeset-color", "--md-typeset-a-color"):
            assert contrast(scheme[key], background) >= 7, key
        assert contrast(scheme["--md-primary-bg-color"], scheme["--md-primary-fg-color"]) >= 7


def test_every_switch_of_the_menu_has_its_style_and_label() -> None:
    script = (ASSETS / "a11y.js").read_text(encoding="utf-8")
    for name in A11Y["toggles"]:
        assert f"html.wiki-a11y-{name}" in CSS, name
        assert MENU[name], name
        assert f'"{name}"' not in script, name  # the names come from config, not from the file
    assert A11Y["storage_key"] not in script
    assert A11Y["text_sizes"][0] == 100 and A11Y["text_sizes"] == sorted(A11Y["text_sizes"])
    assert max(A11Y["text_sizes"]) == 200  # WCAG 1.4.4: up to 200 %


def test_every_page_has_the_accessibility_button_after_the_light_dark_switch(built: Path) -> None:
    sizes, toggles = json.dumps(A11Y["text_sizes"]), json.dumps(A11Y["toggles"])
    for name, html in _pages(built).items():
        header = html.split('<header class="md-header', 1)[1].split("</header>", 1)[0]
        palette, menu = header.index('data-md-component="palette"'), header.index('<div class="wiki-a11y">')
        assert palette < menu < header.index('for="__search"'), name
        button = re.search(r'<button type="button" class="md-header__button md-icon wiki-a11y__button"([^>]*)>', header)
        assert button, name
        assert 'aria-expanded="false"' in button.group(1) and 'aria-controls="wiki-a11y-menu"' in button.group(1), name
        assert f'aria-label="{MENU["title"]}"' in button.group(1), name
        assert re.search(r'<div class="wiki-a11y__menu" id="wiki-a11y-menu" role="group" aria-labelledby="wiki-a11y-title" hidden>', header), name
        # without JavaScript: a plain link to the statement
        assert re.search(r'<a class="md-header__button md-icon wiki-a11y__link" href="(?:\.\./|/)*\.?/?accessibility/"', header) or (
            name == "accessibility/index.html" and 'wiki-a11y__link" href="./"' in header
        ), name
        toggled = re.findall(r'data-wiki-a11y-toggle="([a-z]+)" aria-pressed="false">.*?</span>([^<]+)</button>', header, re.S)
        assert toggled == [(toggle, MENU[toggle]) for toggle in A11Y["toggles"]], name
        for key in ("close", "text_size", "bigger", "smaller", "text_reset", "reset"):
            assert MENU[key] in header, (name, key)
        # Material's icon buttons have Hebrew names
        assert f'for="__drawer" role="button" tabindex="0" aria-expanded="false" aria-label="{A11Y["drawer_label"]}"' in header, name
        assert 'for="__search" aria-label="חיפוש"' in header, name
        assert f"data-text-sizes='{sizes}' data-toggles='{toggles}'" in html, name
        assert f'data-storage-key="{A11Y["storage_key"]}"' in html, name


def test_every_page_is_hebrew_right_to_left_and_asks_no_other_host(built: Path) -> None:
    for name, html in _pages(built).items():
        assert '<html lang="he"' in html and '<body dir="rtl"' in html, name
        assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html, name
        assert not re.search(r'<(?:script|img|iframe)[^>]+src="(?:https?:)?//', html), name
        assert not re.search(r'<link[^>]+rel="stylesheet"[^>]+href="(?:https?:)?//', html), name


def test_the_statement_is_linked_from_every_footer_and_built_like_the_terms(built: Path) -> None:
    footer_link = re.compile(r'<a href="(?:\.\./|/)*(?:accessibility/|\./)">' + re.escape(A11Y["title"]) + "</a>")
    for name, html in _pages(built).items():
        footer = html.split('<footer class="md-footer">', 1)[1]
        lines = footer.split('<div class="wiki-footer-lines">', 1)[1].split("</div>", 1)[0]
        assert footer_link.search(lines), name
        assert lines.index(SITE["terms"]["footer_link_text"]) < lines.index(A11Y["title"]) < lines.index(SITE["coffee_text"])
    html = _pages(built)["accessibility/index.html"]
    assert f"<h1>{A11Y['title']}</h1>" in html
    assert f'href="mailto:{SITE["contact_email"]}"' in unescape(html)
    assert A11Y["updated"] in html
    assert SITE["contact_placeholder"] not in html and A11Y["updated_placeholder"] not in html
    config = yaml.safe_load((built / "mkdocs.yml").read_text(encoding="utf-8"))
    assert "accessibility.md" not in json.dumps(config["nav"]) and "/accessibility.md" in config["not_in_nav"]
    search = json.loads((built / "out" / "search" / "search_index.json").read_text(encoding="utf-8"))
    assert not any(doc["location"].startswith("accessibility/") for doc in search["docs"])


def test_the_statement_names_every_option_of_the_menu_and_no_person() -> None:
    source = (PROJECT_ROOT / A11Y["source"]).read_text(encoding="utf-8")
    assert source.startswith(f"*עודכן לאחרונה: {A11Y['updated_placeholder']}*")
    for key in A11Y["toggles"] + ["bigger", "smaller", "text_reset", "reset"]:
        assert f"**{MENU[key]}**" in source, key
    assert f"**{A11Y['title']}**" in source
    assert f"{max(A11Y['text_sizes'])}%" in source
    assert f"<{SITE['contact_placeholder']}>" in source and SITE["contact_email"] not in source
    assert not re.search(r"\d{2,3}-?\d{7}", source)  # no phone number: the contact is the address only
