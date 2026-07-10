"""Extract prompt seed + entity-hint self-checks."""

from __future__ import annotations

from step_3_extract.run import ENTITY_HINT_HEADER, EXTRACT_SCHEMA, build_extract_prompt
from utils.taxonomy import taxonomy_seed_block, taxonomy_tag_seed

_CLAIM_FIELDS = (
    "claim_text",
    "topic_tags",
    "entities",
    "stance",
    "supporting_message_ids",
    "opposing_message_ids",
)


def test_taxonomy_tag_seed_omits_search_focus() -> None:
    seed = taxonomy_tag_seed()
    assert "search_focus:" not in seed
    assert "argentina:" in seed or "- argentina:" in seed


def test_taxonomy_seed_block_keeps_search_focus_for_plan() -> None:
    assert "search_focus:" in taxonomy_seed_block()


def test_extract_prompt_uses_tag_seed_not_search_focus() -> None:
    prompt = build_extract_prompt("[m0] hello")
    assert "search_focus:" not in prompt
    assert ENTITY_HINT_HEADER not in prompt


def test_extract_prompt_includes_entity_hints() -> None:
    prompt = build_extract_prompt("[m0] hello", entities_hint=["ORM", "  ", "קולומביה"])
    assert ENTITY_HINT_HEADER in prompt
    assert "- ORM" in prompt
    assert "- קולומביה" in prompt


def test_extract_schema_field_descriptions() -> None:
    claims = EXTRACT_SCHEMA["properties"]["claims"]
    assert claims.get("description")
    props = claims["items"]["properties"]
    for name in _CLAIM_FIELDS:
        assert props[name].get("description"), f"{name} missing description"