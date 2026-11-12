"""The parked three-page check of task 27b, run with a fake model on synthetic data."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from step_7_write_pages.consolidation_check import ConsolidationCheck, Measures, _under, main
from step_7_write_pages.run import run
from tests.pages_fixtures import FakeWriter, page_assignments, page_groups
from tests.plan_fixtures import make_plan
from utils.json_io import write_json_file
from utils.paths import AGGREGATED, ASSIGNMENTS, PLAN, WIKI_PAGES, resolve

LOGGER = logging.getLogger("test-check")
CHRONICLE = "בשנת 2022 חברים המליצו על כך.[^g-00000000] ובמאי 2024 נאמרה אותה המלצה.[^g-00000004]"


def _source(root: Path) -> Path:
    """A data dir with groups, plan, assignments and pages written the old way (a chronicle)."""
    groups = page_groups()
    write_json_file({"groups": groups, "metadata": {}}, _under(AGGREGATED, root).original)
    write_json_file(make_plan(), _under(PLAN, root).original)
    write_json_file(page_assignments(groups), _under(ASSIGNMENTS, root).original)
    pages = _under(WIKI_PAGES, root)
    run(aggregated_path=_under(AGGREGATED, root).original, plan_path=_under(PLAN, root).original,
        assignments_path=_under(ASSIGNMENTS, root).original, artifact=pages, llm=FakeWriter(),
        update_llm=FakeWriter("fake-update"))
    payload = resolve(pages).read_text(encoding="utf-8")
    pages.original.write_text(payload.replace("פתיח קצר על הדף.", f"פתיח קצר על הדף. {CHRONICLE}"),
                              encoding="utf-8")
    return root


def test_the_check_writes_the_pages_in_a_copy_and_compares_them(tmp_path: Path,
                                                                 caplog: pytest.LogCaptureFixture
                                                                 ) -> None:
    source = _source(tmp_path / "source")
    before = {p: p.read_bytes() for p in source.rglob("*.json")}
    check = ConsolidationCheck(source, tmp_path / "scratch", ["agencies", "hospital"], LOGGER)
    check.prepare()
    llm = FakeWriter()
    with caplog.at_level(logging.INFO):
        result = check.write(llm, FakeWriter("fake-update"))
        comparisons = check.compare()
    assert sorted(llm.tasks()) == ["write:agencies", "write:hospital"] and result["write"] == 2
    assert {p: p.read_bytes() for p in source.rglob("*.json")} == before  # the source is untouched
    agencies = comparisons[0]
    assert agencies.page_id == "agencies" and CHRONICLE in agencies.before
    assert CHRONICLE not in agencies.after and len(agencies.points) == 3
    old, new = Measures.of(agencies.before), Measures.of(agencies.after)
    assert old.years == 2 and new.years == 0 and old.statements == new.statements + 2
    assert "agencies: words" in caplog.text and "3 points {'fact': 3}" in caplog.text
    path = check.save(comparisons)
    page = path.read_text(encoding="utf-8")
    assert path.parent == tmp_path / "scratch" / "step_7_write_pages"
    assert "Synthetic claim number 8" in page and "<sup>00000004</sup>" in page
    assert page.count("<section>") == 2 and "prefers-color-scheme: dark" in page


def test_the_check_refuses_its_own_source_and_unknown_pages(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path / "source")
    with pytest.raises(ValueError, match="empty scratch folder"):
        ConsolidationCheck(source, source, ["agencies"], LOGGER)
    with pytest.raises(ValueError, match="not in the plan"):
        ConsolidationCheck(source, tmp_path / "scratch", ["nowhere"], LOGGER).prepare()
    monkeypatch.delenv("WIKI_DATA_DIR")
    with pytest.raises(SystemExit):
        main(["--source", str(source)])
