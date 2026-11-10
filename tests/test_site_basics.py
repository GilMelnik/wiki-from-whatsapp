"""Self-check: generated mkdocs config carries site_url + footer wiring, and the footer renders."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from step_9_site.run import build_config
from utils.config import PROJECT_ROOT, section


def _write_site(tmp_path: Path) -> tuple[Path, dict]:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("# בית\n\nשלום\n", encoding="utf-8")
    (docs / "page.md").write_text("# עמוד\n\nתוכן\n", encoding="utf-8")
    config = build_config(docs, tmp_path / "no_plan.json")
    config["theme"]["custom_dir"] = str(PROJECT_ROOT / section("site")["custom_dir"])
    config["extra_css"] = []
    return tmp_path / "mkdocs.yml", config


def test_build_config_has_site_url_and_footer_extra(tmp_path: Path) -> None:
    config = build_config(tmp_path, tmp_path / "no_plan.json")
    site = section("site")
    assert config["site_url"] == site["url"]
    assert config["theme"]["custom_dir"] == site["custom_dir"]
    assert (PROJECT_ROOT / site["custom_dir"] / "main.html").is_file()
    assert config["extra"] == {
        "coffee_text": site["coffee_text"],
        "coffee_link_text": site["coffee_link_text"],
        "coffee_url": site["coffee_url"],
    }


def test_committed_mkdocs_yml_matches_generator() -> None:
    committed = yaml.safe_load((PROJECT_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    generated = build_config(PROJECT_ROOT / "docs")
    for key in ("site_url", "extra"):
        assert committed[key] == generated[key]
    assert committed["theme"]["custom_dir"] == generated["theme"]["custom_dir"]


def test_strict_build_renders_footer_on_every_page(tmp_path: Path) -> None:
    config_path, config = _write_site(tmp_path)
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(config_path), "-d", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    site = section("site")
    for page in (out / "index.html", out / "page" / "index.html", out / "404.html"):
        html = page.read_text(encoding="utf-8")
        assert site["coffee_text"] in html
        assert f'<a href="{site["coffee_url"]}" target="_blank" rel="noopener">{site["coffee_link_text"]}</a>' in html
