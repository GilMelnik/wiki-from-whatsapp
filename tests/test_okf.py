"""OKF front matter: build/validate, render/parse round trip, wiki links, mkdocs compatibility."""

from __future__ import annotations

import copy
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from step_9_site.run import build_config
from utils.config import section
from utils.okf import KEY_ORDER, build_front_matter, parse_page, render_page, validate_front_matter
from utils.wiki_links import WikiLink, find_wiki_links, replace_wiki_links

HEBREW_BODY = '# קולומביה: "מדריך"\n\nטקסט עם \'גרש\' ו-: נקודתיים.\n\n---\n\nעוד פסקה\n---\nסוף\n'


def make_fm(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "title": 'קולומביה: "מדריך" - איך מתחילים',
        "description": "סקירה קצרה של התהליך.",
        "tags": ["קולומביה", "country"],
        "generated_by": "wiki-from-whatsapp/test-model",
        "generated_at": "2026-11-10T09:30:00Z",
        "sources": [
            {
                "id": "g-1a2b3c4d",
                "resource": "claim-group:g-1a2b3c4d",
                "usage_count": 14,
                "last_modified": "2026-10-02",
            }
        ],
        "aliases": {"he": ["קולומביה"], "en": ["Colombia"]},
        "entities": ["Example Clinic"],
        "claims": {
            "total": 12,
            "supporting": 40,
            "opposing": 6,
            "first_claim_at": "2022-03-01",
            "last_claim_at": "2026-10-02",
        },
        "related": [{"slug": "usa", "relation": "sibling"}],
        "path": [{"id": "countries", "title": "לפי מדינה"}],
        "summary": "פסקה אחת שמסכמת את העמוד.",
        "questions": ["כמה זה עולה?", "כמה זמן לוקח?"],
        "labels": [{"id": "colombia", "kind": "country"}],
        "usage_window": {"from": "2022-01-01", "to": "2026-10-31"},
    }
    kwargs.update(overrides)
    return build_front_matter(**kwargs)


def test_build_sets_okf_defaults_in_stable_order() -> None:
    fm = make_fm()
    assert list(fm) == [k for k in KEY_ORDER if k != "status" and k != "verified"]
    assert fm["type"] == "wiki-page"
    assert fm["okf_version"] == section("pages")["okf_version"]
    assert (fm["lang"], fm["dir"]) == ("he", "rtl")
    assert fm["generated"] == {"by": "wiki-from-whatsapp/test-model", "at": "2026-11-10T09:30:00Z"}
    assert fm["stale_after"] == "2027-05-09T09:30:00Z"
    assert fm["sources"][0]["last_modified"] == "2026-10-02T00:00:00Z"


def test_default_status_is_omitted_and_others_kept() -> None:
    assert "status" not in make_fm(status="stable")
    assert make_fm(status="draft")["status"] == "draft"


def test_optional_keys_are_omitted_when_empty() -> None:
    fm = make_fm(summary=None, questions=[], labels=None, verified=[], usage_window=None)
    assert not {"summary", "questions", "labels", "verified", "usage_window"} & fm.keys()


def test_verified_entries_are_kept() -> None:
    fm = make_fm(verified=[{"by": "human:gil", "at": "2026-11-11"}])
    assert fm["verified"] == [{"by": "human:gil", "at": "2026-11-11T00:00:00Z"}]


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"title": ""}, "title"),
        ({"description": "שורה\nשתיים"}, "description"),
        ({"tags": "a"}, "tags"),
        ({"status": "published"}, "status"),
        ({"generated_by": "just-a-name"}, "generated"),
        ({"generated_at": "10/11/2026"}, "generated"),
        ({"generated_at": "2026-11-10T09:30:00"}, "generated"),
        ({"stale_after": "soon"}, "stale_after"),
        ({"sources": [{"id": "g-1"}]}, "sources"),
        ({"sources": [{"id": "g-1", "resource": "x", "usage_count": -1}]}, "sources"),
        ({"aliases": {"he": ["א"]}}, "aliases"),
        ({"aliases": {"he": [], "en": [], "fr": []}}, "aliases"),
        ({"claims": {"total": 1}}, "claims"),
        ({"claims": {"total": 1, "supporting": 1, "opposing": 0, "first_claim_at": "yesterday", "last_claim_at": None}}, "claims"),
        ({"related": [{"slug": "usa", "relation": "Sibling!"}]}, "related"),
        ({"path": ["countries"]}, "path"),
        ({"verified": [{"by": "someone", "at": "2026-11-11"}]}, "verified"),
    ],
)
def test_build_rejects_bad_values_naming_the_key(overrides: dict[str, Any], key: str) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(key))):
        make_fm(**overrides)


def test_validate_reports_missing_required_keys() -> None:
    fm = make_fm()
    del fm["title"], fm["sources"]
    with pytest.raises(ValueError, match="title.*sources"):
        validate_front_matter(fm)
    with pytest.raises(ValueError, match="missing"):
        render_page(fm, "x\n")


def test_validate_allows_unknown_keys_and_render_preserves_them() -> None:
    fm = make_fm()
    fm["x_custom"] = {"a": [1, 2]}
    assert parse_page(render_page(fm, "גוף\n")) == (fm, "גוף\n")


def test_make_fm_does_not_alias_inputs() -> None:
    sources = [{"id": "g-1", "resource": "claim-group:g-1"}]
    fm = make_fm(sources=sources)
    fm["sources"][0]["id"] = "changed"
    assert sources[0]["id"] == "g-1"


@pytest.mark.parametrize(
    "body",
    ["", "plain\n", HEBREW_BODY, "---\n---\n---\n", "```\n[[x]]\n```\n", "שורה\n\n\n  indented\n"],
)
def test_round_trip_is_exact(body: str) -> None:
    fm = make_fm()
    assert parse_page(render_page(fm, body)) == (fm, body)


def test_round_trip_with_hard_values() -> None:
    fm = make_fm(
        title="כותרת: עם 'גרש' ו\"מרכאות\" # ו- --- ",
        description="no",
        tags=["yes", "null", "123", "2026-01-01", "a: b", "---", "[x]"],
        summary="שורה ראשונה\nשורה שנייה: עם נקודתיים\n---\nושלישית",
        questions=[],
        entities=[],
        related=[],
        path=[],
        sources=[],
        aliases={"he": [], "en": []},
    )
    assert parse_page(render_page(fm, "גוף\n")) == (fm, "גוף\n")


def test_render_is_deterministic_hebrew_unescaped_and_block_framed() -> None:
    fm = make_fm()
    first = render_page(fm, HEBREW_BODY)
    assert first == render_page(copy.deepcopy(fm), HEBREW_BODY)
    assert first.startswith("---\nokf_version: '0.2'\ntype: wiki-page\n")
    assert "קולומביה" in first and "\\u" not in first
    assert first.endswith("סוף\n") and not first.endswith("\n\n")
    assert "\n---\n\n# קולומביה" in first


def test_render_normalises_body_newlines() -> None:
    fm = make_fm()
    assert parse_page(render_page(fm, "\n\nטקסט\n\n\n"))[1] == "טקסט\n"


def test_front_matter_is_plain_json_yaml_data() -> None:
    text = render_page(make_fm(), "x\n")
    block = text.split("---\n")[1]
    assert yaml.safe_load(block)["generated"]["by"] == "wiki-from-whatsapp/test-model"


@pytest.mark.parametrize("text", ["no front matter\n", "---\ntitle: x\nno close\n", "---\n- a\n- b\n---\nx"])
def test_parse_rejects_non_pages(text: str) -> None:
    with pytest.raises(ValueError):
        parse_page(text)


def test_parse_accepts_authored_pages_without_blank_line_and_unquoted_timestamps() -> None:
    fm, body = parse_page("---\ntype: Playbook\ngenerated: { by: human:x, at: 2026-06-30T14:00:00Z }\n---\nגוף\n")
    assert fm == {"type": "Playbook", "generated": {"by": "human:x", "at": "2026-06-30T14:00:00Z"}}
    assert body == "גוף\n"


# --- wiki links -----------------------------------------------------------------------------


def test_find_wiki_links_both_forms_with_spans() -> None:
    body = 'ראו [[usa-california|קליפורניה]] ואת [[label:colombia|קולומביה]] או [[egg-donor]].'
    links = find_wiki_links(body)
    assert [(link.kind, link.target, link.text) for link in links] == [
        ("page", "usa-california", "קליפורניה"),
        ("label", "colombia", "קולומביה"),
        ("page", "egg-donor", ""),
    ]
    assert all(body[link.start : link.end].startswith("[[") for link in links)
    assert body[links[0].start : links[0].end] == "[[usa-california|קליפורניה]]"


def test_find_wiki_links_ignores_code_and_malformed() -> None:
    body = (
        "`[[a|inline]]` and ``[[b|`tick`]]`` ok\n"
        "```\n[[c|fenced]]\n```\n"
        "~~~python\n[[d|tilde]]\n~~~\n"
        "[[e|real]] [[f|unclosed\n[[ spaced |x]] [[g|a\nb]] [[h|ok]]\n"
        "````\n```\n[[i|nested fence]]\n```\n````\n[[j|after]]\n"
    )
    assert [link.target for link in find_wiki_links(body)] == ["e", "h", "j"]


def test_find_wiki_links_unterminated_fence_hides_rest() -> None:
    assert find_wiki_links("[[a|x]]\n```\n[[b|y]]\n") == [WikiLink("page", "a", "x", 0, 7)]


def test_find_wiki_links_nested_brackets() -> None:
    body = 'א [[usa|ארה"ב [חדש]]] ב [[p|x [y [z]] w]] ג [[q|bad ] text]] ד'
    links = find_wiki_links(body)
    assert [(link.target, link.text) for link in links] == [("usa", 'ארה"ב [חדש]'), ("p", "x [y [z]] w")]


def test_replace_wiki_links() -> None:
    body = "[[a|אלף]] ו-[[label:b|בית]] ו-[[c|גימל]] `[[a|code]]` [[d]]\n"

    def resolver(link: WikiLink) -> str | None:
        if link.target == "c":
            return None
        return f"[{link.text}](/{link.kind}/{link.target}/)"

    assert replace_wiki_links(body, resolver) == (
        "[אלף](/page/a/) ו-[בית](/label/b/) ו-גימל `[[a|code]]` [](/page/d/)\n"
    )
    assert replace_wiki_links("[[d]] x", lambda link: None) == "d x"
    assert replace_wiki_links("no links", lambda link: "!") == "no links"


# --- mkdocs compatibility -------------------------------------------------------------------


def _build_site(tmp_path: Path, pages: dict[str, str]) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name, text in pages.items():
        (docs / name).write_text(text, encoding="utf-8")
    config_path = tmp_path / "mkdocs.yml"
    config = build_config([{name: name} for name in pages], docs_dir=docs, mkdocs_path=config_path)
    config["extra_css"] = []
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(config_path), "-d", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr
    return out


def _article(html: str) -> str:
    return re.search(r"<article.*?</article>", html, re.DOTALL).group(0)  # type: ignore[union-attr]


def test_mkdocs_strict_build_renders_none_of_the_front_matter(tmp_path: Path) -> None:
    fm = make_fm(summary="סיכום-סודי-למכונות", questions=["שאלה-סודית?"], entities=["ישות-סודית"])
    body = "# קולומביה\n\nתוכן **גלוי** לקורא.\n"
    out = _build_site(
        tmp_path,
        {"index.md": render_page(fm, body), "page.md": render_page(make_fm(title="עמוד שני"), "# שני\n\nעוד\n")},
    )
    hidden = [
        "okf_version", "wiki-page", "סיכום-סודי-למכונות", "שאלה-סודית", "ישות-סודית", "claim-group:",
        "g-1a2b3c4d", "wiki-from-whatsapp/test-model", "stale_after", "usage_window", "2027-05-09",
        "sibling", "first_claim_at",
    ]  # fmt: skip
    for page in (out / "index.html", out / "page" / "index.html"):
        html = page.read_text(encoding="utf-8")
        article = _article(html)
        assert "תוכן" in article or "עוד" in article
        for token in hidden:
            assert token not in html, f"{token!r} leaked into {page.name}"
        assert "md-status" not in html and "md-tag" not in html
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert fm["title"].replace('"', "&quot;") in index_html or fm["title"] in index_html
    assert f'<meta name="description" content="{fm["description"]}">' in index_html
    assert "סיכום-סודי-למכונות" not in (out / "search" / "search_index.json").read_text(encoding="utf-8")


def test_non_default_status_shows_a_material_nav_badge(tmp_path: Path) -> None:
    """Why ``stable`` is never written: Material renders any ``status`` as a nav badge."""
    out = _build_site(tmp_path, {"index.md": render_page(make_fm(status="draft"), "# א\n")})
    assert "md-status--draft" in (out / "index.html").read_text(encoding="utf-8")
