"""Prompt-caching layout checks: breakpoints land where intended and the
reordered community prompt still flattens to text the mock/disk-cache can parse.
"""

from __future__ import annotations

from step_7_community.run import build_batch_prompt
from step_7_community.store import PageStore
from utils.llm_client import CacheSegment, _flatten, _to_blocks

EPHEMERAL = {"type": "ephemeral"}


def test_to_blocks_marks_cache_breakpoints():
    # A plain string system prompt is auto-cached at its (only) block.
    assert _to_blocks("sys", cache_last=True) == [
        {"type": "text", "text": "sys", "cache_control": EPHEMERAL}
    ]
    # Without cache_last a bare string carries no breakpoint.
    assert _to_blocks("sys") == [{"type": "text", "text": "sys"}]

    user = [CacheSegment("catalog", cache=True), CacheSegment("variable")]
    blocks = _to_blocks(user)
    assert blocks[0]["cache_control"] == EPHEMERAL  # catalog prefix is cached
    assert "cache_control" not in blocks[1]  # variable tail is not


def test_build_batch_prompt_layout_and_flatten():
    claims = [
        {
            "claim_id": "t1-c1",
            "stance": "positive",
            "claim_text": "מדינה זו מתאימה.",
            "topic_tags": ["usa"],
        }
    ]
    store = PageStore({c["claim_id"]: c for c in claims}, {})
    store.ensure_page("usa", title="ארהב", category="emergent")

    segments = build_batch_prompt(store, "usa", claims, resolver=None)

    # Cached, almost-static catalog first; uncached per-call tail second.
    assert [s.cache for s in segments] == [True, False]

    flat = _flatten(segments)
    # Mock (_mock_community_agent) and the disk cache both key off the flat text,
    # so the reorder must preserve these anchors.
    assert "עמוד נוכחי:" in flat
    assert "claim_id: t1-c1" in flat
    assert "קטלוג עמודים" in flat
