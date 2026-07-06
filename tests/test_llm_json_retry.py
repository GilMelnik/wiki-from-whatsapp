"""complete_json/complete_batch_json JSON handling.

max_tokens grows only when a response was truncated at the token limit; a
parse failure that is *not* a truncation is logged and dropped (a bigger window
cannot fix bad formatting). Unparseable responses are copied to the ``*_bad``
cache and never kept in the good cache.
"""

from __future__ import annotations

import json

import pytest

from utils.llm_client import MAX_TOKENS_CEILING, BatchRequest, LLMClient


def _client(tmp_path, responses):
    """A client whose _dispatch yields queued ``(text, truncated)`` in order."""

    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    queue = list(responses)
    llm._dispatch = lambda system, user, task="", json_mode=False, response_schema=None: queue.pop(0)  # type: ignore[method-assign]
    return llm


def test_truncated_then_good_bumps_and_caches_only_good(tmp_path):
    # First attempt truncated at max_tokens, second is valid.
    llm = _client(tmp_path, [('{"a": 1', True), ('{"a": 1}', False)])
    assert llm.complete_json("sys", "usr") == {"a": 1}
    assert llm.max_tokens == 8192  # doubled once after the truncated attempt

    # Only the valid response was cached; a re-run reads it back without dispatch.
    llm._dispatch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call"))
    assert llm.complete_json("sys", "usr") == {"a": 1}


def test_non_truncation_parse_failure_is_not_retried(tmp_path):
    """Prose (not a truncation) must not grow the window or retry — just log."""

    llm = _client(tmp_path, [("sorry, here are the claims...", False)] * 5)
    with pytest.raises(ValueError):
        llm.complete_json("sys", "usr", task="t")

    assert llm.max_tokens == 4096  # never bumped
    lines = (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # a single attempt, then give up
    assert json.loads(lines[0])["kind"] == "parse_error"
    # Bad answer copied to the sibling *_bad cache for inspection.
    key = llm._cache_key("sys", "usr")
    assert (llm.bad_cache_dir / f"{key}.json").exists()


def test_stale_bad_cache_is_dropped_and_rerequested(tmp_path):
    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    key = llm._cache_key("sys", "usr")
    llm._write_cache(key, '{"a": 1')  # truncated answer left by a prior run

    llm._dispatch = lambda *a, **k: ('{"a": 2}', False)  # type: ignore[method-assign]
    assert llm.complete_json("sys", "usr") == {"a": 2}
    # A clean re-request does not need more room, so max_tokens is unchanged.
    assert llm.max_tokens == 4096
    assert llm._read_cache(key) is not None  # replaced with the good response


def test_gives_up_at_ceiling_and_logs_failure(tmp_path):
    llm = _client(tmp_path, [('{"bad"', True)] * 20)  # truncated, at the ceiling
    llm.max_tokens = MAX_TOKENS_CEILING  # already maxed out
    with pytest.raises(ValueError):
        llm.complete_json("sys", "usr", task="t")

    lines = (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "parse_error"
    assert entry["task"] == "t"
    assert entry["response"] == '{"bad"'  # raw output kept for inspection


def test_complete_batch_json_resubmits_only_truncated_as_batch(tmp_path):
    """Good items are used as-is; only the truncated one is re-submitted — as
    another batch — with a doubled max_tokens window until it parses.
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
        # First round: "ok" valid, "bad" truncated. Retry round: "bad" ok.
        return {
            r.request_id: (
                (('{"v": 2}', False) if llm.max_tokens > 4096 else ('{"v": 2', True))
                if r.request_id == "bad"
                else ('{"v": 1}', False)
            )
            for r in requests
        }

    llm.complete_batch = _batch  # type: ignore[method-assign]

    result = llm.complete_batch_json(reqs)

    assert result == {"ok": {"v": 1}, "bad": {"v": 2}}
    assert batches == [["ok", "bad"], ["bad"]]  # retry batch holds only the failure
    assert llm.max_tokens == 8192  # doubled for the retry batch
    assert not (tmp_path / "failures.jsonl").exists()  # healed -> no failure logged


def test_complete_batch_json_drops_non_truncation_without_retry(tmp_path):
    """A batch item that is prose (not truncated) is logged and dropped, never
    re-submitted, and max_tokens is left alone.
    """

    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=4096,
        failure_log=tmp_path / "failures.jsonl",
    )
    req = BatchRequest(request_id="bad", system="s", user="u", task="extract")
    batches: list[list[str]] = []

    def _batch(requests):
        batches.append([r.request_id for r in requests])
        return {"bad": ("here are the claims i found", False)}

    llm.complete_batch = _batch  # type: ignore[method-assign]

    result = llm.complete_batch_json([req])

    assert result == {}  # omitted; caller skips it
    assert batches == [["bad"]]  # submitted once, not retried
    assert llm.max_tokens == 4096  # window untouched
    entry = json.loads((tmp_path / "failures.jsonl").read_text().splitlines()[0])
    assert entry["kind"] == "parse_error"
    key = llm._cache_key("s", "u")
    assert (llm.bad_cache_dir / f"{key}.json").exists()


def test_complete_batch_json_records_when_ceiling_reached(tmp_path):
    llm = LLMClient(
        provider="mock",
        cache_dir=tmp_path,
        max_tokens=MAX_TOKENS_CEILING,  # no room left to grow
        failure_log=tmp_path / "failures.jsonl",
    )
    req = BatchRequest(request_id="bad", system="s", user="u", task="extract")
    llm.complete_batch = lambda requests: {"bad": ('{"v": 2', True)}  # type: ignore[method-assign]

    result = llm.complete_batch_json([req])

    assert result == {}  # omitted; caller skips it
    entry = json.loads((tmp_path / "failures.jsonl").read_text().splitlines()[0])
    assert entry["kind"] == "parse_error"
    assert entry["task"] == "extract"
    assert entry["response"] == '{"v": 2'
