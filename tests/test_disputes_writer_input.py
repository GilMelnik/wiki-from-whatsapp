"""From a dispute recorded in step 3 to the page writer's prompt (read-only on step 7).

Step 5 runs on two synthetic claims that dispute each other (C3 ``counters``); step 7's own
input builder then makes the prompt of a full write (no model call). The other side's text
must reach the writer, as task 25b designed it: on the same page as its own group line, on
another page inside the ``contradicts`` field with that page's id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import step_5_aggregate.similarity as similarity
from step_5_aggregate.run import run
from step_7_write_pages.inputs import Corpus as PagesCorpus
from step_7_write_pages.prompts import write_prompt
from tests.aggregate_fixtures import Corpus, angle_for
from tests.plan_fixtures import make_plan
from utils.json_io import write_json_file
from utils.llm_client import _flatten

CLAIM = "Agency Alpha handled the paperwork well"
DISSENT = "Agency Alpha lost documents and delayed the process"


@pytest.fixture
def groups_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    corpus = Corpus(tmp_path)
    monkeypatch.setattr(similarity, "embed_texts", corpus.embed)
    corpus.add("a1", text=CLAIM, entities=["Alpha"], date="2024-01-01", stance="neutral",
               supporters=[("p1", "2024-01-01")], opposers=[("p2", "2024-01-02")])
    corpus.add("a2", text=DISSENT, angle=angle_for(0.999), entities=["Alpha"],
               date="2024-01-02", stance="neutral", supporters=[("p2", "2024-01-02")])
    corpus.link("a1", "a2")
    run(**corpus.paths())
    return corpus.artifact.edited


def _prompt(tmp_path: Path, groups_path: Path, pages: dict[str, list[str]]) -> tuple[str, dict]:
    groups = json.loads(groups_path.read_text("utf-8"))["groups"]
    by_claim = {g["representative_claim_id"]: g["group_id"] for g in groups}
    assignments = {by_claim[c]: {"pages": p, "labels": [], "p": {}, "label_p": {}}
                   for c, p in pages.items()}
    plan_path, assignments_path = tmp_path / "plan.json", tmp_path / "assignments.json"
    write_json_file(make_plan(), plan_path)
    write_json_file({"assignments": assignments, "proposals": [], "metadata": {}},
                    assignments_path)
    corpus = PagesCorpus.load(groups_path, plan_path, assignments_path, tmp_path / "entities.json")
    context = corpus.context("agencies", corpus.catalogue())
    assert set(context.required_ids()) == {by_claim["a1"]} | (
        {by_claim["a2"]} if "agencies" in pages["a2"] else set())
    return _flatten(write_prompt(context)[1]), by_claim


def test_both_sides_on_one_page(tmp_path: Path, groups_path: Path) -> None:
    user, ids = _prompt(tmp_path, groups_path, {"a1": ["agencies"], "a2": ["agencies"]})
    lines = {gid: next(line for line in user.splitlines() if line.startswith(gid))
             for gid in ids.values()}
    assert f"contradicts: {ids['a2']}" in lines[ids["a1"]] and CLAIM in lines[ids["a1"]]
    assert f"contradicts: {ids['a1']}" in lines[ids["a2"]] and DISSENT in lines[ids["a2"]]
    assert "disputed by 1" in lines[ids["a1"]]


def test_the_other_side_on_another_page_comes_with_its_text(
    tmp_path: Path, groups_path: Path
) -> None:
    user, ids = _prompt(tmp_path, groups_path, {"a1": ["agencies"], "a2": ["costs"]})
    line = next(line for line in user.splitlines() if line.startswith(ids["a1"]))
    assert f'contradicts: {ids["a2"]} (on costs: "{DISSENT}")' in line
