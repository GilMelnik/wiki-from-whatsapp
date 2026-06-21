"""Step 4b: resolve free-text entity strings into a global canonical registry.

Claims carry free-text ``entities`` (hundreds of distinct strings) where the
same real entity shows up as many variants (``תמוז`` / ``tamuz`` / ``Tammuz``,
``כללית`` / ``קופת חולים כללית``, ``David Shield`` / ``DavidShield``). This step
clusters those strings into suggested canonical entities so that step 5 stops
fragmenting per-entity stances.

Similarity is deterministic and high-precision: a normalized string-similarity
signal (catches same-script spelling variants) plus a cross-script
transliteration signal (a Hebrew->Latin consonant skeleton, which catches
``דייויד שילד`` <-> ``David Shield``, ``עמית פלס`` <-> ``Amit Peles``). The human
merges whatever is left in the review tool.

ponytail: semantic sentence embeddings were measured and deliberately rejected
here. For short proper nouns multilingual-e5 encodes *category*, not identity, so
distinct same-type entities score higher than true transliterations
(``תל אביב``/``ירושלים`` = 0.96 but ``David Shield``/``דיוויד שילד`` = 0.90) — there
is no usable threshold. Taxonomy ``keywords`` are NOT used as a merge signal for
the same reason: a page like ``providers-other`` lists many distinct agencies
that share a topic but are not the same entity.

Output: ``data/entities.json`` (suggested clusters; the reviewer writes
``data/entities_edited.json``). Contact details (emails/phones from the PII
redaction trail, websites from claim text) are preserved per entity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.json_io import write_json_file

from step_4b_entities.cluster import cluster_entities
from step_4b_entities.collect import collect_entities, load_claims_for_entities
from step_4b_entities.constants import DEFAULT_OUTPUT_PATH, SIMILARITY_THRESHOLD


def run(
    claims_path: Path | str | None = None,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    claims, resolved_claims, original_by_id = load_claims_for_entities(claims_path)

    entities = collect_entities(claims, original_by_id=original_by_id)
    out_entities = cluster_entities(
        entities,
        resolved_claims,
        similarity_threshold=similarity_threshold,
    )

    output = {
        "entities": out_entities,
        "metadata": {
            "source": str(resolved_claims),
            "distinct_entity_count": len(entities),
            "entity_count": len(out_entities),
            "multi_member_count": sum(1 for e in out_entities if len(e["members"]) > 1),
            "ambiguous_count": sum(1 for e in out_entities if e.get("status") == "ambiguous"),
            "merge_method": "multi_signal_v2",
            "similarity_threshold": similarity_threshold,
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


if __name__ == "__main__":
    meta = run(similarity_threshold=0.80)
    print(
        f"{meta['entity_count']} entities "
        f"({meta['multi_member_count']} multi-member) "
        f"from {meta['distinct_entity_count']} distinct strings "
        f"via {meta['merge_method']}"
    )

    # Cleanup of the rejected embedding-cache experiment, if present.
    _stale = Path("data/entity_embeddings.json")
    if _stale.exists():
        _stale.unlink()
