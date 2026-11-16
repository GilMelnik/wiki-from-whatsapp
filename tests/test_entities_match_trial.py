"""The trial tool of step 4's model stages: numbers on the console, names only in its file.
Fakes only; every name is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_4_entities import match_trial
from step_4_entities.mentions import SimpleAnalyzer
from tests.entities_match_support import FakeClassifier, FakeEmbed, FakeLLM, by_keyword, claim, grouped

CLAIMS = [
    claim("t1-a", ["Dana Levi"], "Levi won in court", "2023-01-01"),
    claim("t2-a", ["Dana"], "Dana court order", "2023-02-01"),
    claim("t3-a", ["Dana"], "Dana insurance paid", "2023-03-01"),
    claim("t4-a", ["MoH"], "MoH papers", "2023-04-01"),
    claim("t5-a", ["Ministry of Health"], "Ministry of Health papers", "2023-05-01"),
]
NAMES = {"Dana Levi": ["t1-a"], "Dana": ["t2-a", "t3-a"], "MoH": ["t4-a"],
         "Ministry of Health": ["t5-a"]}


@pytest.fixture
def trial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    entities = tmp_path / "entities.json"
    entities.write_text(json.dumps({"entities": [
        {"entity_id": f"e{i}", "members": [{"name": n, "claim_ids": c, "count": len(c)}]}
        for i, (n, c) in enumerate(NAMES.items())]}), encoding="utf-8")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"entities": [{"canonical": "Dana Levi", "aliases": ["Dana"]},
                                             {"canonical": "Ministry of Health",
                                              "aliases": ["MoH"]}]}), encoding="utf-8")
    llm = FakeLLM(grouped({"Dana": ["lawyer", "insurer"], "Dana Levi": ["lawyer"]}),
                  by_keyword({"lawyer": ["court"], "insurer": ["insur"]}))
    monkeypatch.setattr(match_trial, "load_claims_for_entities",
                        lambda: (CLAIMS, tmp_path / "claims.json", None))
    monkeypatch.setattr(match_trial, "resolve", lambda artifact: entities)
    monkeypatch.setattr(match_trial, "ENTITIES_SEED_PATH", seed)
    monkeypatch.setattr(match_trial, "ENTITY_ANALYSIS_PATH", tmp_path / "analysis.json")
    monkeypatch.setattr(match_trial, "DictaAnalyzer", SimpleAnalyzer)
    monkeypatch.setattr(match_trial, "default_embed",
                        lambda dry: FakeEmbed({"MoH": "Ministry of Health"}))
    monkeypatch.setattr(match_trial.LLMClient, "for_stage", lambda *a, **k: llm)
    monkeypatch.setattr(match_trial.Classifier, "for_stage",
                        lambda *a, **k: FakeClassifier(lambda a, b: 0.9))
    return llm


def _console(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_the_net_trial_scores_against_the_seed(trial: FakeLLM,
                                               caplog: pytest.LogCaptureFixture) -> None:
    match_trial.main(["--net"])
    console = _console(caplog)
    assert "'seed_pairs': 2" in console and "'seed_pairs_in_net': 2" in console
    assert all(name not in console for name in NAMES)


def test_the_judge_trial_divides_a_name_with_two_meanings(
        trial: FakeLLM, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    listed = tmp_path / "bundles.json"
    listed.write_text(json.dumps([["Dana", "Dana Levi"]]), encoding="utf-8")
    match_trial.main(["--judge", str(listed)])
    console = _console(caplog)
    assert "'names_with_two_meanings': 1" in console and "'claims_per_meaning': [[1, 1]]" in console
    assert "'seed_units_rebuilt': 1" in console
    assert all(name not in console for name in NAMES)
    written = sorted((match_trial.step_path(match_trial.STEP_4, "match_trial")).glob("judge_*"))
    detail = json.loads(written[-1].read_text(encoding="utf-8"))
    assert detail["senses"][0]["claims"] == {"t2-a": ["lawyer"], "t3-a": ["insurer"]}


def test_the_screen_trial_reports_the_share_kept_per_bar(trial: FakeLLM,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    match_trial.main(["--screen"])
    console = _console(caplog)
    assert "'kept_share_by_bar'" in console and "'0.5': {'seed': 1.0" in console
    assert all(name not in console for name in NAMES)
