"""complete_json recovers from truncated output: never caches unparseable
responses, drops a bad cache entry from a prior run, and bumps max_tokens.
"""

from __future__ import annotations

import json

import pytest

from utils.llm_client import MAX_TOKENS_CEILING, BatchRequest, LLMClient


def _client(tmp_path, responses):
    """A client whose _dispatch yields queued responses in order."""

    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    queue = list(responses)
    llm._dispatch = lambda system, user, task="": queue.pop(0)  # type: ignore[method-assign]
    return llm


def test_bad_then_good_bumps_and_caches_only_good(tmp_path):
    llm = _client(tmp_path, ['{"a": 1', '{"a": 1}'])  # truncated, then valid
    assert llm.complete_json("sys", "usr") == {"a": 1}
    assert llm.max_tokens == 8192  # doubled once after the bad attempt

    # Only the valid response was cached; a re-run reads it back without dispatch.
    llm._dispatch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call"))
    assert llm.complete_json("sys", "usr") == {"a": 1}


def test_stale_bad_cache_is_dropped_and_rerequested(tmp_path):
    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    key = llm._cache_key("sys", "usr")
    llm._write_cache(key, '{"a": 1')  # truncated answer left by a prior run

    llm._dispatch = lambda *a, **k: '{"a": 2}'  # type: ignore[method-assign]
    assert llm.complete_json("sys", "usr") == {"a": 2}
    assert llm.max_tokens == 8192  # bumped when the stale cache failed to parse
    assert llm._read_cache(key) is not None  # replaced with the good response


def test_gives_up_at_ceiling_and_logs_failure(tmp_path):
    llm = _client(tmp_path, ['{"bad"'] * 20)
    llm.max_tokens = MAX_TOKENS_CEILING  # already maxed out
    with pytest.raises(ValueError):
        llm.complete_json("sys", "usr", task="t")

    lines = (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "parse_error"
    assert entry["task"] == "t"
    assert entry["response"] == '{"bad"'  # raw output kept for inspection


def test_complete_batch_json_resubmits_only_failed_as_batch(tmp_path):
    """Good batch items are used as-is; only the truncated one is re-submitted —
    as another batch — with a doubled max_tokens window until it parses.
    """

    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    reqs = [
        BatchRequest(request_id="ok", system="s1", user="u1", task="classify"),
        BatchRequest(request_id="bad", system="s2", user="u2", task="classify"),
    ]
    batches: list[list[str]] = []

    def _batch(requests):
        ids = [r.request_id for r in requests]
        batches.append(ids)
        # First round: "ok" is valid, "bad" is truncated. Retry round: "bad" ok.
        return {r.request_id: ('{"v": 2}' if llm.max_tokens > 4096 else '{"v": 2')
                if r.request_id == "bad" else '{"v": 1}' for r in requests}

    llm.complete_batch = _batch  # type: ignore[method-assign]

    result = llm.complete_batch_json(reqs)

    assert result == {"ok": {"v": 1}, "bad": {"v": 2}}
    assert batches == [["ok", "bad"], ["bad"]]  # retry batch holds only the failure
    assert llm.max_tokens == 8192  # doubled for the retry batch
    assert not (tmp_path / "failures.jsonl").exists()  # healed -> no failure logged


def test_complete_batch_json_records_when_ceiling_reached(tmp_path):
    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=MAX_TOKENS_CEILING,  # no room left to grow
        failure_log=tmp_path / "failures.jsonl",
    )
    req = BatchRequest(request_id="bad", system="s", user="u", task="extract")
    llm.complete_batch = lambda requests: {"bad": '{"v": 2'}  # type: ignore[method-assign]

    result = llm.complete_batch_json([req])

    assert result == {}  # omitted; caller skips it
    entry = json.loads((tmp_path / "failures.jsonl").read_text().splitlines()[0])
    assert entry["kind"] == "parse_error"
    assert entry["task"] == "extract"
    assert entry["response"] == '{"v": 2'
