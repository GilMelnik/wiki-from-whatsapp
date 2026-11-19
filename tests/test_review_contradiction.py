"""A contradiction applied from the review is saved as the owner's manual pair and survives:
the claim records join it on every load (also after the claims are written again), and step 8
writes both sides (synthetic claim records and fake page writers)."""

from __future__ import annotations

import json
from pathlib import Path

from review_tool.tools.suggestions.apply import Applier
from step_5_same_complaint.claim_records import load_claim_records, load_manual_pairs
from tests.pages_fixtures import IDS, FakeWriter, PagesWorkspace
from tests.review_fixtures import corrector, long_article
from utils.llm_client import _flatten
from utils.paths import Artifact


def test_a_manual_contradiction_link_survives_new_claims_and_reaches_the_writer(
        tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    ws.run(llm=FakeWriter(respond=long_article))
    before = {claim["short_id"]: claim for claim in load_claim_records(ws.sources)}
    assert IDS[2] not in before[IDS[0]]["contradicts"]
    applier = Applier(pages_artifact=ws.artifact,
                      plan_artifact=Artifact(ws.plan_path, tmp_path / "plan_edited.json"),
                      sources=ws.sources, assignments_path=ws.assignments_path,
                      llm=FakeWriter("fake-apply", respond=corrector("פתיח קצר", "שני צדדים")))
    suggestion = {"kind": "contradiction", "proposal": {
        "claims_a": [IDS[0]], "claims_b": [IDS[2]], "pages": ["agencies", "clinics"],
        "clash": "סותרות", "correction": "להציג את שני הצדדים"}}
    applier.apply(suggestion)
    assert load_manual_pairs(ws.sources.contradictions) == [tuple(sorted((IDS[0], IDS[2])))]
    assert "שני צדדים" in ws.pages(edited=True)["pages"]["agencies"]["body_md"]
    payload = json.loads(ws.sources.claims.read_text(encoding="utf-8"))
    payload["claims"][11]["claim_text"] = "Reworded by a later step 3 run."  # an unplaced claim
    ws.sources.claims.write_text(json.dumps(payload), encoding="utf-8")
    claims = {claim["short_id"]: claim for claim in load_claim_records(ws.sources)}
    assert IDS[2] in claims[IDS[0]]["contradicts"] and IDS[0] in claims[IDS[2]]["contradicts"]
    update = FakeWriter("fake-update")
    ws.run(update_llm=update)
    prompts = {call["task"]: _flatten(call["user"]) for call in update.calls}
    assert set(prompts) == {"update:agencies", "update:clinics"}
    line = next(line for line in prompts["update:agencies"].splitlines()
                if line.startswith(f"changed: {IDS[0]} |"))
    assert f"contradicts: {IDS[2]} (on clinics:" in line
