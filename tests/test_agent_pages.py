"""Markdown for AI agents: the twin of every page, ``llms.txt``, the head link, the search left alone
(built with ``mkdocs build --strict``)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from step_6_plan import operations
from step_9_site.agent_pages import plain_admonitions, strip_marks, twin_docs_path, twin_url
from step_9_site.run import build_config
from tests.site_fixtures import make_page, make_site_plan, run_site
from utils.config import PROJECT_ROOT, section

SITE = section("site")
AGENT = SITE["agent_markdown"]
BASE = SITE["url"]
BODY = (
    "## רקע\n\nטענה ראשונה.[^g-00000001] ראו [[clinics|המרפאות]] ו־[[costs|העלויות]] ו־[[label:usa|ארה\"ב]] "
    "ו־[[nowhere|עמוד חסר]] [^g-00000002][^g-00000003].\n\n- פריט [^g-00000001]\n\n[^g-00000001]: ישן.\n"
)
MARK = re.compile(r"\[\^")
HTML_TAG = re.compile(r"</?[a-zA-Z][^>\n]*>")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A strict build with a merged page (a redirect stub), a label page and a body with every kind of link."""
    tmp_path = tmp_path_factory.mktemp("agent_pages")
    plan = operations.merge(make_site_plan(), "agencies", ["costs"]).plan
    pages = {
        "agencies": make_page(plan, "agencies", BODY, labels=("usa",)),
        "clinics": make_page(plan, "clinics", "טקסט.\n"),
    }
    shutil.copytree(PROJECT_ROOT / "docs" / "stylesheets", tmp_path / "docs" / "stylesheets")
    run_site(tmp_path, plan, pages)
    command = [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(tmp_path / "mkdocs.yml"), "-d"]
    built_site = subprocess.run([*command, str(tmp_path / "out")], capture_output=True, text=True, check=False)
    assert built_site.returncode == 0, built_site.stderr
    return tmp_path


def _site_file(built: Path, url: str) -> Path:
    """The built file a twin link points to."""
    assert url.startswith(BASE), url
    return built / "out" / url.removeprefix(BASE)


def test_strip_marks_removes_marks_and_the_space_before_them() -> None:
    assert strip_marks("א [^g-0000000a]. ב.[^g-0000000a][^g-0000000b]\nג [^g-0000000a] ד\n") == "א. ב.\nג ד\n"
    assert strip_marks("א[^g-0000000a]ב") == "א ב"
    assert strip_marks("[^note] stays") == "[^note] stays"


def test_plain_admonitions_become_block_quotes() -> None:
    text = '!!! warning "כותרת"\n    שורה ראשונה\n\n    שורה שנייה\n\nאחרי\n'
    assert plain_admonitions(text) == "> **כותרת**\n> שורה ראשונה\n>\n> שורה שנייה\n\nאחרי\n"
    assert "!!!" not in plain_admonitions('!!! info\n    טקסט\n')


def test_twin_paths_mirror_the_built_site() -> None:
    assert twin_docs_path("index.md") == f"{AGENT['dir']}/index.md"
    assert twin_docs_path("agencies.md") == f"{AGENT['dir']}/agencies/index.md"
    assert twin_docs_path("labels/usa.md") == f"{AGENT['dir']}/labels/usa/index.md"
    assert twin_url("labels/usa.md") == f"{BASE}labels/usa/index.md"
    assert twin_url("index.md") == f"{BASE}index.md"


def test_generated_config_keeps_twins_out_of_the_build_and_loads_the_hook() -> None:
    config = build_config([], docs_dir=PROJECT_ROOT / "docs", mkdocs_path=PROJECT_ROOT / "mkdocs.yml")
    assert config["exclude_docs"] == f"/{AGENT['dir']}/"
    assert any(hook.endswith("agent_hook.py") for hook in config["hooks"])
    assert any(hook.endswith("hebrew_search.py") for hook in config["hooks"])
    assert config["extra"]["agent_markdown"] == {"dir": AGENT["dir"], "twin_file": AGENT["twin_file"]}


def test_twins_exist_for_every_page_and_only_for_pages(built: Path) -> None:
    out = built / "out"
    twins = {path.relative_to(out).as_posix() for path in out.rglob(AGENT["twin_file"])}
    assert twins == {
        "index.md",
        "about/index.md",
        "terms/index.md",
        "agencies/index.md",
        "clinics/index.md",
        "labels/usa/index.md",
    }
    # the stub of the merged page and the 404 page have no twin
    assert not (out / "costs" / "index.md").exists()
    assert not (built / "docs" / AGENT["dir"] / "costs").exists()
    assert (out / "agencies" / "index.md").read_bytes() == (built / "docs" / AGENT["dir"] / "agencies" / "index.md").read_bytes()


def test_every_twin_starts_with_the_disclaimer_and_has_no_marks_or_html(built: Path) -> None:
    disclaimer = SITE["disclaimer"]
    for path in (built / "out").rglob(AGENT["twin_file"]):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# "), path
        assert disclaimer["text"] in text.split("\n\n")[1], path
        header = text.split("\n\n")[1]
        assert f"({BASE}terms/index.md)" in header and SITE["contact_email"] not in header, path
        assert not MARK.search(text), path
        assert not HTML_TAG.search(text), path
        assert "[[" not in text and "!!!" not in text, path
        assert not text.startswith("---"), path  # no front matter


def test_links_in_the_twins_are_absolute_and_resolve_to_twins(built: Path) -> None:
    for path in (built / "out").rglob(AGENT["twin_file"]):
        for url in MD_LINK.findall(path.read_text(encoding="utf-8")):
            if url.startswith(BASE):
                target = _site_file(built, url)
                assert target.is_file() and target.name == AGENT["twin_file"], (path, url)
            else:
                assert url.startswith("https://"), (path, url)
    page = (built / "out" / "agencies" / "index.md").read_text(encoding="utf-8")
    assert f"[המרפאות]({BASE}clinics/index.md)" in page
    assert f"[ארה\"ב]({BASE}labels/usa/index.md)" in page
    assert f"[העלויות]({BASE}agencies/index.md)" in page  # the link to the merged page follows its survivor
    assert "עמוד חסר" in page and "nowhere" not in page


def test_wiki_page_twin_shape(built: Path) -> None:
    text = (built / "out" / "agencies" / "index.md").read_text(encoding="utf-8")
    assert text.startswith("# " + json.loads((built / "plan.json").read_text("utf-8"))["pages"]["agencies"]["title_he"])
    assert f"Web page: {BASE}agencies/" in text
    assert "טענה ראשונה. ראו" in text and "- פריט\n" in text
    assert "ישן" not in text  # a stored definition is not shown
    assert "Page generated 2026-11-01." in text and "dated 2024-01-01 to 2025-01-01" in text
    assert "claims" in text.rsplit("---", 1)[1]


def test_twin_and_the_index_text_hold_the_same_words(built: Path) -> None:
    """``get_page`` returns the index ``text``; the twin is that text with its links and marks changed."""
    index = json.loads((built / "docs" / "wiki_index.json").read_text(encoding="utf-8"))

    def words(text: str) -> list[str]:
        text = re.sub(r"\[\[(?:label:)?([^|\]]+)(?:\|([^\]]*))?\]\]", lambda m: m.group(2) or m.group(1), text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        return re.findall(r"\w+", re.sub(r"\[\^[^\]]+\]", " ", text))

    for page in index["pages"]:
        twin = (built / "out" / page["id"] / AGENT["twin_file"]).read_text(encoding="utf-8")
        body = twin.split("Web page: ", 1)[1].split("\n\n", 1)[1].rsplit("\n---\n", 1)[0]
        assert words(body) == words(page["text"]), page["id"]


def test_llms_txt_lists_every_page_once_with_a_working_link(built: Path) -> None:
    text = (built / "out" / AGENT["llms_file"]).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == f"# {SITE['mkdocs']['site_name']}"
    assert lines[2].startswith("> ") and lines[2] == f"> {AGENT['llms']['summary']}"
    assert SITE["disclaimer"]["text"] in text
    assert f"{BASE}mcp" in text
    urls = [url for line in lines if line.startswith("- [") for url in MD_LINK.findall(line)]
    assert len(urls) == len(set(urls))
    expected = {twin_url(f"{name}.md") for name in ("agencies", "clinics", "labels/usa", "about", "terms")}
    assert expected <= set(urls)
    assert twin_url("costs.md") not in urls  # a redirect stub is not a page
    for url in urls:
        assert _site_file(built, url).is_file(), url
    index = json.loads((built / "docs" / "wiki_index.json").read_text(encoding="utf-8"))
    for page in index["pages"]:
        assert f"]({BASE}{page['id']}/index.md): {page['description']}" in text
    assert text.rstrip().splitlines()[-1] == f"- [{SITE['terms']['title']}]({twin_url('terms.md')})"
    assert text.index(f"## {AGENT['llms']['about_heading']}") > text.index(f"({BASE}clinics/index.md)")


def test_pages_point_to_their_twin_in_the_head(built: Path) -> None:
    link = '<link rel="alternate" type="text/markdown" href="{}">'
    for name, twin in {
        "index.html": f"{BASE}index.md",
        "agencies/index.html": f"{BASE}agencies/index.md",
        "labels/usa/index.html": f"{BASE}labels/usa/index.md",
        "about/index.html": f"{BASE}about/index.md",
        "terms/index.html": f"{BASE}terms/index.md",
    }.items():
        html = (built / "out" / name).read_text(encoding="utf-8")
        assert link.format(twin) in html and html.index(link.format(twin)) < html.index("</head>"), name
    for name in ("costs/index.html", "404.html"):  # a redirect stub and the 404 page have no twin
        assert 'rel="alternate"' not in (built / "out" / name).read_text(encoding="utf-8"), name


def test_the_search_does_not_index_the_new_files(built: Path) -> None:
    index = json.loads((built / "out" / "search" / "search_index.json").read_text(encoding="utf-8"))
    locations = {doc["location"] for doc in index["docs"]}
    assert not any(AGENT["twin_file"] in location or "llms" in location or "_agent" in location for location in locations)
    assert {location.split("#")[0] for location in locations} <= {
        "", "agencies/", "clinics/", "labels/usa/", "labels/georgia/",
    }
    assert not (built / "out" / AGENT["dir"]).exists()  # the folder itself is not shipped


def test_step_9_owns_the_agent_files(built: Path) -> None:
    mkdocs = yaml.safe_load((built / "mkdocs.yml").read_text(encoding="utf-8"))
    assert mkdocs["exclude_docs"] == f"/{AGENT['dir']}/"
    assert (built / "docs" / AGENT["llms_file"]).is_file()
    assert (built / "docs" / AGENT["dir"] / "index.md").is_file()
