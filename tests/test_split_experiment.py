"""Task 52's split experiment on a synthetic chat with fake models (no paid call)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from step_3_extract.claims import ThreadClaims
from step_3_extract.split_experiment.compare import VariantRun, match, metrics, table
from step_3_extract.split_experiment.prompts import UnitJob
from step_3_extract.split_experiment.run import (
    OUTPUT_DIR,
    CostCapError,
    DryRunModel,
    Experiment,
    main,
)
from step_3_extract.split_experiment.units import (
    EXPERIMENT_CONFIG,
    MessageSet,
    Unit,
    build_units,
    check_cover,
    pack,
    thread_owner,
    with_background,
)
from step_3_extract.split_experiment.usage import reported_usd
from tests.split_experiment_support import (
    NEW_SPLIT,
    OLD_SPLIT,
    USAGE,
    FakeBatches,
    chat,
    claim,
    gold,
    message_set,
    threads,
    write_inputs,
)
from utils.llm_client import LLMClient

LOGGER = logging.getLogger("test-split-experiment")


def _units() -> dict[str, list[Unit]]:
    return build_units(message_set(), threads(NEW_SPLIT), threads(OLD_SPLIT))


def _ids(units: list[Unit]) -> dict[str, list[list[str]]]:
    return {u.unit_id: [list(c) for c in u.conversations] for u in units}


def test_every_variant_cuts_the_same_messages() -> None:
    units = _units()
    # t-a starts before the set and t-b goes on after it: both are cut to the set.
    assert _ids(units["A"]) == {"A1-01": [["m04", "m05"]], "A1-02": [["m06", "m07", "m08", "m09"]],
                                "A2-01": [["m14", "m15"]], "A2-02": [["m16"]]}
    assert _ids(units["B"]) == {"B1-01": [["m04", "m05"]], "B1-02": [["m06", "m07", "m08"]],
                                "B1-03": [["m09"]], "B2-01": [["m14", "m15", "m16"]]}
    assert _ids(units["C"]) == {"C1-01": [["m04", "m05", "m06", "m07", "m08", "m09"]],
                                "C2-01": [["m14", "m15", "m16"]]}
    assert [u.unit_id for u in units["D"]] == ["D1-01", "D1-02", "D2-01", "D2-02"]
    assert units["D"][0].background == ("m01", "m02", "m03")  # fewer than 8 before it
    assert units["D"][1].background == ("m01", "m02", "m03", "m04", "m05")
    assert units["D"][2].background[-1] == "m13" and len(units["D"][2].background) == 8
    # Everything fits one pack per window; each pack keeps its conversations apart.
    assert _ids(units["E"]) == {"E1-01": [["m04", "m05"], ["m06", "m07", "m08", "m09"]],
                                "E2-01": [["m14", "m15"], ["m16"]]}
    assert units["E"][0].source == "A1-01 A1-02"
    assert {u.window for u in units["B"]} == {0, 1}


def test_background_count_packing_limit_and_cover_check() -> None:
    messages = message_set()
    new_units = _units()["A"]
    assert with_background(new_units, messages, 2)[1].background == ("m04", "m05")
    assert len(pack(new_units, messages, 1)) == 4  # nothing fits together: one unit per call
    with pytest.raises(ValueError, match="0 extra, 2 missing"):
        check_cover(new_units[1:], messages)  # A1-01 left out
    with pytest.raises(ValueError, match="2 extra, 0 missing"):
        check_cover([*new_units, new_units[0]], messages)
    with pytest.raises(ValueError, match="in no thread"):
        build_units(messages, threads({"t-a": ["m04"]}), threads(OLD_SPLIT))


def test_a_plain_unit_is_sent_exactly_as_step_3_sends_a_thread() -> None:
    messages = message_set()
    unit = _units()["A"][1]
    expected = ThreadClaims(messages.thread(unit.unit_id, unit.message_ids)).prompt([])
    assert UnitJob(unit, messages).prompt() == expected
    assert expected.startswith("Conversation:\n[1] ") and "↩[1]" in expected  # m07 replies to m06


def test_background_messages_never_become_evidence() -> None:
    messages = message_set()
    job = UnitJob(_units()["D"][1], messages)  # background m01..m05 = [1]..[5]; m06.. = [6]..
    prompt = job.prompt()
    assert "messages [1] to [5]" in prompt and "Background (read only):\n[1] " in prompt
    assert "\n\nConversation:\n[6] " in prompt and "synthetic text m06" in prompt.split(
        "Conversation:")[-1]
    answer = {"claims": [claim("Only background", [2]), claim("Mixed", [3, 7], "positive"),
                         claim("Clean", [6, 8])]}
    outcome = job.claims(answer)
    assert outcome.background_citing == 2 and outcome.dropped == 1
    assert [c["claim_text"] for c in outcome.claims] == ["Mixed", "Clean"]
    assert [c["cites_background"] for c in outcome.claims] == [True, False]
    background = set(job.unit.background)
    assert all(not background & set(c["supporting_message_ids"]) for c in outcome.claims)
    assert outcome.claims[0]["supporting_message_ids"] == ["m07"]


def test_packed_claims_map_to_their_conversations() -> None:
    messages = message_set()
    job = UnitJob(_units()["E"][0], messages)
    prompt = job.prompt()
    assert prompt.startswith("This request holds 2 separate conversations")
    assert "Conversation 1 of 2:\n[1] " in prompt and "\n\nConversation 2 of 2:\n[3] " in prompt
    outcome = job.claims({"claims": [claim("Across", [1, 3]), claim("Second only", [4], "negative",
                                                                    counters=[1])]})
    across, second = outcome.claims
    assert across["conversations"] == 2 and second["conversations"] == 1
    assert across["supporting_message_ids"] == ["m04", "m06"] and across["unit_id"] == "E1-01"
    assert second["counters"] == [across["claim_id"]]


def _claim(claim_id: str, ids: list[str], window: int = 0, **fields: Any) -> dict[str, Any]:
    return {"claim_id": claim_id, "window": window, "supporting_message_ids": ids,
            "stance": "positive", "supporter_count": 1, "counters": [], "conversations": 1,
            **fields}


def test_matching_is_one_to_one_by_overlap_within_a_window() -> None:
    reference = [_claim("r1", ["m1", "m2"]), _claim("r2", ["m3"]), _claim("r3", ["m9"], 1)]
    other = [_claim("o1", ["m1"]), _claim("o2", ["m1", "m2"]), _claim("o3", ["m5"]),
             _claim("o4", ["m3", "m4", "m6"]), _claim("o5", ["m9"])]
    # o2 is the best match of r1, so o1 gets none; o4 shares 1 of r2's 1 message (overlap 1.0);
    # o5 rests on r3's message but in another window.
    assert match(reference, other, 0.5) == {"o2": "r1", "o4": "r2"}
    assert match(reference, other, 1.01) == {}


def test_the_table_counts_claims_failures_and_matches() -> None:
    reference = VariantRun("B", [], claims=[_claim("r1", ["m1"]), _claim("r2", ["m2"])])
    other = VariantRun("A", [], claims=[
        _claim("a1", ["m1"], supporter_count=2, counters=["a2"]),
        _claim("a2", ["m4"], stance="negative", counters=["a1"], conversations=2),
        _claim("a3", ["m4"])], schema_failures=["A1-02"], dropped=1, ungrounded=2)
    set_ids = {"m1", "m2", "m3", "m4"}
    columns = table({"B": reference, "A": other}, set_ids, {"A": match(reference.claims,
                                                                      other.claims)})
    a = columns["A"]
    assert (a["claims"], a["claims_per_100"], a["stance_positive"], a["stance_negative"]) == (
        3, 75.0, 2, 1)
    assert (a["two_supporters"], a["dispute_links"], a["unclaimed_messages"]) == (1, 1, 2)
    assert (a["multi_conversation"], a["schema_failures"], a["dropped"]) == (1, 1, 3)
    assert (a["found"], a["missed"], a["extra"]) == (1, 1, 2)
    assert "found" not in columns["B"]
    assert "found" not in metrics(other, set_ids, None, 0)


def _experiment(llm: Any, out_dir: Path, *, use_batch: bool = True) -> Experiment:
    return Experiment(_units(), message_set(), llm, LOGGER, use_batch=use_batch, out_dir=out_dir)


def test_the_estimate_and_the_refusal_above_the_cap(tmp_path: Path) -> None:
    model = DryRunModel()
    experiment = _experiment(model, tmp_path)
    estimate = experiment.estimate()
    assert estimate["total"]["calls"] == 4 + 4 + 2 + 4 + 2
    assert all(estimate[v]["input_tokens"] > 0 and estimate[v]["usd"] > 0 for v in "ABCDE")
    assert estimate["C"]["input_tokens"] < estimate["A"]["input_tokens"]  # fewer instructions
    sync = _experiment(model, tmp_path, use_batch=False).estimate()
    assert sync["total"]["usd"] > estimate["total"]["usd"]
    with pytest.raises(CostCapError, match="above the cap"):
        experiment.run(max_usd=estimate["total"]["usd"] / 2)
    assert model.usage == []  # refused before any call


def _anthropic(cache: Path) -> tuple[LLMClient, FakeBatches]:
    llm = LLMClient(provider="anthropic", model="claude-sonnet-5-5", cache_dir=cache,
                    failure_log=cache.parent / "failures.jsonl", thinking_param="low")
    batches = FakeBatches()
    llm._provider._client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    return llm, batches


def test_costs_come_from_the_api_and_a_repeated_run_uses_the_cache(tmp_path: Path) -> None:
    llm, batches = _anthropic(tmp_path / "cache")
    first = _experiment(llm, tmp_path / "out").run(max_usd=None)
    assert batches.created == 1 and len(batches.submitted) == 16
    one_call = reported_usd(USAGE, batch=True)
    for variant, run in first.items():
        calls = len(run.units)
        assert run.fresh_calls == calls and run.cached_calls == 0
        assert run.usage == {key: value * calls for key, value in USAGE.items()}
        assert run.usd == pytest.approx(one_call * calls)
        # One claim on message [1] per answer; in D that is a background message.
        assert len(run.claims) == (0 if variant == "D" else calls)
        assert run.background_citing == (calls if variant == "D" else 0)
    assert one_call == pytest.approx(
        (100 + 10 * 1.25 + 50 * 0.1) * 2.0 / 1e6 * 0.5 + 20 * 10.0 / 1e6 * 0.5)

    again_llm, again_batches = _anthropic(tmp_path / "cache")
    again = _experiment(again_llm, tmp_path / "out").run(max_usd=None)
    assert again_batches.created == 0  # every answer came from the disk cache
    for variant, run in again.items():
        assert run.fresh_calls == 0 and run.cached_calls == len(run.units)
        assert run.usage == first[variant].usage and run.usd == pytest.approx(first[variant].usd)
        assert [c["claim_id"] for c in run.claims] == [c["claim_id"] for c in first[variant].claims]


class FailingModel(DryRunModel):
    """The sync path: one unit's call fails, one answer has the wrong shape."""

    def complete_json(self, system: str, user: str, task: str = "",
                      response_schema: dict[str, Any] | None = None) -> Any:
        if "synthetic text m14" in user and "synthetic text m16" not in user:
            raise RuntimeError("provider down")
        if "synthetic text m09" in user and "synthetic text m08" not in user:
            return {"claims": "not a list"}
        return super().complete_json(system, user, task, response_schema)


def test_failed_calls_and_schema_failures_are_counted(tmp_path: Path) -> None:
    runs = _experiment(FailingModel(), tmp_path, use_batch=False).run(max_usd=None)
    assert runs["A"].no_answer == ["A2-01"] and runs["D"].no_answer == ["D2-01"]
    assert runs["B"].schema_failures == ["B1-03"]
    assert runs["A"].fresh_calls == 3 and runs["A"].unpriced_calls == 0
    assert not [c for c in runs["B"].claims if c["unit_id"] == "B1-03"]


def test_the_page_shows_every_window_and_variant(tmp_path: Path) -> None:
    experiment = _experiment(DryRunModel(), tmp_path)
    runs = experiment.run(max_usd=None)
    page = experiment.save(runs, {"dry_run": True, "model": "dry-run", "batch": True}
                           ).read_text(encoding="utf-8")
    assert "id='w1'" in page and "id='w2'" in page and page.count("<section") == 2
    for variant, name in EXPERIMENT_CONFIG["variants"].items():
        assert f"<th>{variant} <small>{name.replace(chr(39), '&#x27;')}</small></th>" in page
    assert "synthetic text m09" in page and "Dry run" in page
    assert "prefers-color-scheme: dark" in page and "<script" not in page and "http" not in page
    assert "no claim for B" in page and "conversation 2 of 2" in page and "background" in page
    table = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))["table"]
    assert set(table) == set("ABCDE") and table["E"]["units"] == 2
    results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert set(results["matches"]) == set("ACDE")


def test_the_command_line_estimates_dry_runs_and_refuses(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          caplog: pytest.LogCaptureFixture
                                                          ) -> None:
    paths = write_inputs(tmp_path)
    args = [arg for name, path in paths.items() for arg in (f"--{name}", str(path))]
    with caplog.at_level(logging.INFO):
        main([*args, "--estimate"])
    assert "Estimate total: 16 calls" in caplog.text and not OUTPUT_DIR.exists()
    main([*args, "--dry-run", "--variants", "a,b", "--windows", "2"])
    dry = OUTPUT_DIR / EXPERIMENT_CONFIG["dry_run_dir"]
    table = json.loads((dry / "table.json").read_text(encoding="utf-8"))
    assert set(table["table"]) == {"A", "B"} and table["metadata"]["windows"] == [2]
    assert table["table"]["A"]["units"] == 2 and (dry / EXPERIMENT_CONFIG["page_file"]).is_file()
    monkeypatch.setitem(EXPERIMENT_CONFIG, "max_usd", 0.0)
    with pytest.raises(SystemExit, match="refused"):
        main(args)
    assert not (OUTPUT_DIR / "results.json").exists()
    with pytest.raises(SystemExit):
        main([*args, "--variants", "Z"])


def test_a_window_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ValueError, match="no gold window \\[3\\]"):
        MessageSet(gold(), chat(), [2])
    assert thread_owner(threads(NEW_SPLIT))["m09"] == "t-b"


class _Stream:
    """``messages.stream(...)`` of a fake Anthropic client: one answer reporting ``USAGE``."""

    def __call__(self, **params: Any) -> _Stream:
        return self

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> SimpleNamespace:
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"claims": []}')],
                               stop_reason="end_turn", stop_details=None,
                               usage=SimpleNamespace(**USAGE))


def test_a_sync_call_keeps_the_usage_the_api_reported(tmp_path: Path) -> None:
    llm, _ = _anthropic(tmp_path / "cache")
    llm._provider._client = SimpleNamespace(messages=SimpleNamespace(stream=_Stream()))
    assert llm.complete_json("system", "user") == {"claims": []}
    assert llm.usage == [{"request_id": None, **USAGE}]
    llm.complete_json("system", "user")  # from the disk cache: nothing reported
    assert len(llm.usage) == 1
