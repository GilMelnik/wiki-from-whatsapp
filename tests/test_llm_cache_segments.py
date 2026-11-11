"""Prompt-caching layout checks: breakpoints land where intended and the page writer's
prompt still flattens to text the disk cache can key on.
"""

from __future__ import annotations

from pathlib import Path

from step_7_write_pages.inputs import Corpus
from step_7_write_pages.prompts import write_prompt
from tests.pages_fixtures import PagesWorkspace
from utils.llm_client import CacheSegment, _flatten, _to_blocks

EPHEMERAL = {"type": "ephemeral"}


def test_to_blocks_marks_cache_breakpoints() -> None:
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


def test_write_prompt_layout_and_flatten(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    corpus = Corpus.load(*ws.paths())
    catalogue = corpus.catalogue()
    first = write_prompt(corpus.context("agencies", catalogue))
    second = write_prompt(corpus.context("costs", catalogue))

    # The cached, run-wide catalogue first; the page's own material in the uncached tail.
    segments = first[1]
    assert [s.cache for s in segments] == [True, False]
    assert first[0] == second[0] and segments[0] == second[1][0]  # identical cached prefix
    flat = _flatten(segments)
    assert "<catalogue>" in flat and "id: agencies" in flat and "g-00000000 | main |" in flat
