"""From a dispute recorded in step 3 to the page writer's prompt (read-only on step 7).

Two synthetic claims dispute each other (C3 ``counters``); the claim records join them as each
other's ``contradicts`` and step 7's own input builder then makes the prompt of a full write
(no model call). The other side's text must reach the writer, as task 25b designed it: on the
same page as its own claim line, on another page inside the ``contradicts`` field with that
page's id.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from step_5_same_complaint.claim_records import ClaimSources
from step_8_write_pages.inputs import Corpus
from step_8_write_pages.prompts import write_prompt
from tests.claim_fixtures import claim_record, write_claim_inputs
from tests.plan_fixtures import make_plan
from utils.json_io import write_json_file
from utils.llm_client import _flatten

CLAIM = "Agency Alpha handled the paperwork well"
DISSENT = "Agency Alpha lost documents and delayed the process"


@pytest.fixture
def sources(tmp_path: Path) -> ClaimSources:
    """Claim ``a1`` (backed by one member, disputed by another) and claim ``a2`` (the dispute,
    said by the second member), linked as step 3's ``counters``."""
    alpha = {"entity_ids": ["e0001"], "entity_names": ["Alpha"], "stance": "neutral"}
    a1 = claim_record(0, claim_text=CLAIM, first_date="2024-01-01", last_date="2024-01-01",
                      supporter_count=1, opposer_count=1, **alpha)
    a2 = claim_record(1, claim_text=DISSENT, first_date="2024-01-02", last_date="2024-01-02",
                      supporter_count=1, opposer_count=0, contradicts=[a1["short_id"]], **alpha)
    a1["contradicts"] = [a2["short_id"]]
    return write_claim_inputs([a1, a2], tmp_path / "claims")


def _prompt(tmp_path: Path, sources: ClaimSources, pages: dict[str, list[str]]
            ) -> tuple[str, dict[str, str]]:
    ids = {"a1": claim_record(0)["short_id"], "a2": claim_record(1)["short_id"]}
    assignments = {ids[c]: {"pages": p, "labels": [], "p": {}, "label_p": {}}
                   for c, p in pages.items()}
    plan_path, assignments_path = tmp_path / "plan.json", tmp_path / "assignments.json"
    write_json_file(make_plan(), plan_path)
    write_json_file({"assignments": assignments, "proposals": [], "metadata": {}},
                    assignments_path)
    corpus = Corpus.load(sources, plan_path, assignments_path)
    context = corpus.context("agencies", corpus.catalogue())
    assert set(context.required_ids()) == {ids["a1"]} | (
        {ids["a2"]} if "agencies" in pages["a2"] else set())
    return _flatten(write_prompt(context)[1]), ids


def test_both_sides_on_one_page(tmp_path: Path, sources: ClaimSources) -> None:
    user, ids = _prompt(tmp_path, sources, {"a1": ["agencies"], "a2": ["agencies"]})
    lines = {sid: next(line for line in user.splitlines() if line.startswith(sid))
             for sid in ids.values()}
    assert f"contradicts: {ids['a2']}" in lines[ids["a1"]] and CLAIM in lines[ids["a1"]]
    assert f"contradicts: {ids['a1']}" in lines[ids["a2"]] and DISSENT in lines[ids["a2"]]
    assert "disputed by 1" in lines[ids["a1"]]


def test_the_other_side_on_another_page_comes_with_its_text(
    tmp_path: Path, sources: ClaimSources
) -> None:
    user, ids = _prompt(tmp_path, sources, {"a1": ["agencies"], "a2": ["costs"]})
    line = next(line for line in user.splitlines() if line.startswith(ids["a1"]))
    assert f'contradicts: {ids["a2"]} (on costs: "{DISSENT}")' in line
