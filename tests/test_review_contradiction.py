"""A contradiction applied through the groups tool's real store survives a step-5 re-run
(synthetic step-5 corpus, fake embedder and fake page writer)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_5_aggregate.reviewer.store import GroupsStore
from step_7_write_pages.run import run as write_pages
from step_8_review.apply import Applier
from tests.groups_tool_support import GroupsSetup
from tests.pages_fixtures import FakeWriter
from tests.plan_fixtures import PAGE_IDS, make_plan
from tests.review_fixtures import corrector, long_article
from utils.json_io import write_json_file
from utils.paths import Artifact, resolve


def _assign(groups: list[dict[str, Any]], pages: dict[str, str]) -> dict[str, Any]:
    records = {g["group_id"]: {"pages": [pages.get(g["group_id"], PAGE_IDS[i % 4])],
                               "labels": [], "p": {}, "label_p": {}}
               for i, g in enumerate(groups)}
    return {"assignments": records, "proposals": [], "metadata": {}}


def test_a_manual_contradiction_link_survives_step_5(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    setup = GroupsSetup(tmp_path, monkeypatch)
    d1, c1 = setup.gid("d1"), setup.gid("c1")
    assert c1 not in setup.group_of("d1")["contradicts"]
    plan_path, assignments_path = tmp_path / "plan.json", tmp_path / "assignments.json"
    write_json_file(make_plan(), plan_path)
    write_json_file(_assign(setup.payload()["groups"], {d1: "agencies", c1: "clinics"}),
                    assignments_path)
    pages = Artifact(tmp_path / "wiki_pages.json", tmp_path / "wiki_pages_edited.json")
    write_pages(aggregated_path=resolve(setup.corpus.artifact), plan_path=plan_path,
                assignments_path=assignments_path, artifact=pages,
                llm=FakeWriter(respond=long_article), update_llm=FakeWriter("fake-update"))

    def linker() -> GroupsStore:
        return GroupsStore(setup.graph.node("groups"), setup.graph.node("claims"),
                           setup.graph.node("entities"), tmp_path / "audit.json")

    applier = Applier(pages_artifact=pages,
                      plan_artifact=Artifact(plan_path, tmp_path / "plan_edited.json"),
                      aggregated_path=resolve(setup.corpus.artifact),
                      assignments_path=assignments_path,
                      llm=FakeWriter("fake-apply", respond=corrector("פתיח קצר", "שני צדדים")),
                      linker=linker)
    suggestion = {"kind": "contradiction", "proposal": {
        "groups_a": [d1], "groups_b": [c1], "pages": ["agencies", "clinics"],
        "clash": "סותרות", "correction": "להציג את שני הצדדים"}}
    applier.apply(suggestion)
    assert setup.group_of("d1")["contradicts_manual"] == [c1]
    assert d1 in setup.group_of("c1")["contradicts"]
    setup.run_step()
    assert c1 in setup.group_of("d1")["contradicts"]
    assert setup.group_of("c1")["contradicts_manual"] == [d1]
    assert "שני צדדים" in setup.payload(pages.edited)["pages"]["agencies"]["body_md"]
