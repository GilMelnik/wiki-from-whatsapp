"""``mkdocs build --strict`` on step 9's output: clean, footer kept, metadata hidden, search fix."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from step_6_plan import operations
from tests.site_fixtures import make_page, make_site_plan, run_site
from utils.config import PROJECT_ROOT, section

BODY = (
    "## רקע\n\nבקולומביה יש [[clinics|מרפאות]] ו-[[label:usa|תגית]].[^g-00000001]\n\n"
    "[^g-00000001]: קבוצת טענות.\n"
)


def test_generated_site_builds_strict_with_footer_hidden_metadata_and_search_variants(
    tmp_path: Path,
) -> None:
    plan = operations.merge(make_site_plan(), "agencies", ["costs"]).plan
    pages = {
        "agencies": make_page(plan, "agencies", BODY, labels=("usa",), summary="סיכום-נסתר"),
        "clinics": make_page(plan, "clinics", "טקסט.\n", status="draft"),
    }
    shutil.copytree(PROJECT_ROOT / "docs" / "stylesheets", tmp_path / "docs" / "stylesheets")
    run_site(tmp_path, plan, pages)
    out = tmp_path / "site"
    built = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(tmp_path / "mkdocs.yml"), "-d", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    assert "WARNING" not in built.stderr
    site = section("site")
    pages_html = {
        name: (out / name).read_text(encoding="utf-8")
        for name in ("index.html", "agencies/index.html", "labels/usa/index.html", "costs/index.html", "404.html")
    }
    for html in pages_html.values():
        assert site["coffee_url"] in html and site["coffee_text"] in html
    agencies = pages_html["agencies/index.html"]
    for hidden in ("okf_version", "claim-group:", "סיכום-נסתר", "stale_after", "wiki-from-whatsapp/test-model"):
        assert all(hidden not in html for html in pages_html.values()), hidden
    assert '<a href="../clinics/">מרפאות</a>' in agencies
    assert '<a href="../labels/usa/">תגית</a>' in agencies
    assert 'class="footnote"' in agencies
    assert 'http-equiv="refresh" content="0; url=../agencies/"' in pages_html["costs/index.html"]
    assert (out / section("mcp")["index_file"]).is_file()
    search = json.loads((out / "search" / "search_index.json").read_text(encoding="utf-8"))
    field = site["hebrew_search"]["field"]
    assert field in search["config"]["fields"]
    assert any("קולומביה" in doc.get(field, "").split() for doc in search["docs"])
    assert not any(doc["location"].startswith("costs/") for doc in search["docs"])  # stub excluded
