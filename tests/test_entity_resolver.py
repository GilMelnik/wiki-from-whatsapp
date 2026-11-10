"""Step 5's entity resolver on step-4 entity payloads (moved out of test_entities.py)."""

from __future__ import annotations

import json
from pathlib import Path

from step_5_aggregate.resolver import EntityResolver, apply_entity_resolution, load_entity_resolver


def _claim(claim_id: str, entities: list[str]) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.split("-")[0],
        "claim_text": " / ".join(entities),
        "entities": entities,
    }


def test_resolver_maps_name_and_claim_overrides() -> None:
    registry_entities = [
        {
            "entity_id": "e0000",
            "canonical_name": "תמוז",
            "aliases": ["תמוז", "Tammuz"],
            "members": [
                {"name": "תמוז", "claim_ids": None},
                {"name": "Tammuz", "claim_ids": None},
            ],
            "contacts": {"email": [], "phone": [], "website": []},
            "topics": ["tamuz"],
        },
        {
            "entity_id": "e0001",
            "canonical_name": "מיכל קרן דוד",
            "aliases": ["מיכל"],
            "members": [{"name": "מיכל", "claim_ids": ["t9-c0"]}],
            "contacts": {"email": [], "phone": [], "website": []},
            "topics": ["providers-other"],
        },
    ]
    resolver = EntityResolver(registry_entities)

    assert resolver.canonical("Tammuz") == "תמוז"
    assert resolver.canonical("מיכל", "t9-c0") == "מיכל קרן דוד"  # claim override
    assert resolver.canonical("מיכל", "t1-c0") == "מיכל"  # no override -> identity
    assert resolver.canonical("unknown") == "unknown"

    claim = _claim("t1-c0", ["Tammuz", "תמוז"])
    assert resolver.resolve_claim(claim) == ["תמוז"]  # deduped to canonical
    assert "תמוז" in resolver.registry()


def test_apply_resolution_and_missing_file_fallback(tmp_path: Path) -> None:
    # Missing file -> no resolver, claims untouched.
    missing = tmp_path / "nope.json"
    assert load_entity_resolver(missing) is None
    claim = _claim("t1-c0", ["אלעל"])
    apply_entity_resolution([claim], None)
    assert claim["entities"] == ["אלעל"]

    # Present file -> claims rewritten to canonical names.
    path = tmp_path / "entities.json"
    path.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "entity_id": "e0000",
                        "canonical_name": "אל על",
                        "aliases": ["אל על", "אלעל"],
                        "members": [
                            {"name": "אל על", "claim_ids": None},
                            {"name": "אלעל", "claim_ids": None},
                        ],
                        "contacts": {},
                        "topics": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    resolver = load_entity_resolver(path)
    assert resolver is not None
    claim2 = _claim("t1-c0", ["אלעל"])
    apply_entity_resolution([claim2], resolver)
    assert claim2["entities"] == ["אל על"]
