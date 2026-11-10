"""Step 4: resolve the free-text entity strings of C3 claims into canonical entities.

Claims carry free-text ``entities`` where one real entity shows up as many variants
(``תמוז`` / ``tamuz`` / ``Tammuz``, ``כללית`` / ``קופת חולים כללית``). This step groups them
into suggested canonical entities that the owner reviews in the entity reviewer, and tells
step 5 which canonical entities every claim mentions (``claim_entities``).

Inputs: only ``claim_id``, ``thread_id``, ``claim_text`` and ``entities`` of each claim (plus
the redaction trail for contacts). Nothing else about a claim is read.

Similarity is deterministic and high-precision (see ``pair_index``). Semantic embeddings were
measured and rejected for short proper nouns: they encode the category, not the identity
(two different cities score higher than a true transliteration pair).

Reconcile (C14, custom merge): ``metadata.inputs`` holds a fingerprint per claim (entity
strings + text). A re-run folds only new / changed / removed claims into the CURRENT EDITED
entities (``EntityUpdate``), so the reviewer's merges, splits, renames, exclusions and
contact decisions survive; both files are written with ``guarded_write`` (edited first).
``--rebuild`` re-clusters from scratch after backing up the edited file.

Output (``entities.json`` / ``entities_edited.json``)::

    {"entities": [{entity_id, canonical_name, status, aliases, members: [{name, claim_ids,
       count, sample_claim_ids, contacts, contacts_uncertain, excluded_claim_ids?,
       attached_by?}], contacts, contacts_uncertain, merge_signals, score, conflict_with?,
       type?, contacts_manual?, contacts_rejected?, canonical_manual?}],
     "claim_entities": {claim_id: [entity_id]},
     "deleted_claims"?: [claim_id], "deleted_names"?: [name],
     "metadata": {inputs, run, params, last_entity_number, ...}}
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from step_4_entities.cluster import EntityClusterer
from step_4_entities.collect import ClaimCorpus, load_claims_for_entities, tagged_names
from step_4_entities.mentions import Analyzer, DictaAnalyzer, build_or_load_analysis
from step_4_entities.records import entity_counts, member_names
from step_4_entities.update import EntityUpdate
from utils.config import ENTITIES_CONFIG
from utils.embedding_ab import _git_commit
from utils.logging_setup import setup_step_logging
from utils.paths import (
    ENTITIES,
    ENTITIES_SEED_PATH,
    ENTITY_ANALYSIS_PATH,
    STEP_4,
    Artifact,
)
from utils.reconcile import diff_inputs, fingerprint, guarded_write, load_previous

PARAM_KEYS = (
    "similarity_threshold",
    "prefix_similarity",
    "min_skeleton_len",
    "short_name_max_chars",
    "guard_min_context",
    "guard_cap",
    "merge_method",
)


def claim_fingerprint(claim: dict[str, Any]) -> str:
    """What step 4 reads from a claim: its entity strings and its text."""

    return fingerprint(
        {"entities": tagged_names(claim), "claim_text": claim.get("claim_text") or ""}
    )


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _metadata(
    state: dict[str, Any], inputs: dict[str, str], source: Path, stats: dict[str, int]
) -> dict[str, Any]:
    return {
        "source": str(source),
        "inputs": inputs,
        "run": stats,
        "params": {key: ENTITIES_CONFIG[key] for key in PARAM_KEYS},
        "analyzer_model": ENTITIES_CONFIG["analyzer"]["model"],
        "git_commit": _git_commit(),
        "last_entity_number": state.get("metadata", {}).get("last_entity_number", -1),
        **entity_counts(state),
    }


def run(
    *,
    claims_path: Path | str | None = None,
    artifact: Artifact = ENTITIES,
    analyzer: Analyzer | None = None,
    analysis_path: Path = ENTITY_ANALYSIS_PATH,
    seed_path: Path = ENTITIES_SEED_PATH,
    only: Collection[str] | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    """Create or incrementally update the canonical entities; see the module docstring.

    ``claims_path`` defaults to the reviewed (edited) claims when they exist. ``only``
    restricts the work to these claim ids (other claims keep their entities and
    fingerprints); ``rebuild`` re-clusters everything (the edited file is backed up first,
    entity ids keep counting up). ``analyzer`` defaults to dictabert (``DictaAnalyzer``).
    Returns the run counters plus ``entity_count`` and ``backup``.
    """

    logger = setup_step_logging(STEP_4)
    claims, resolved, original_by_id = load_claims_for_entities(claims_path)
    previous = load_previous(artifact)
    if rebuild:
        last = previous.get("metadata", {}).get("last_entity_number", -1)
        state: dict[str, Any] = {"metadata": {"last_entity_number": last}}
    else:
        state = _read(artifact.edited) or previous
        # A name in the step's last output that no edited member carries any more was
        # deleted by the reviewer: never bring it back.
        deleted = (member_names(previous) - member_names(state)) if previous else set()
        if deleted or state.get("deleted_names"):
            state["deleted_names"] = sorted(deleted | set(state.get("deleted_names") or []))

    scope = None if only is None else set(only)
    inputs = {
        c["claim_id"]: claim_fingerprint(c)
        for c in claims
        if scope is None or c["claim_id"] in scope
    }
    prev_inputs = {} if rebuild else (previous.get("metadata", {}).get("inputs") or {})
    diff = diff_inputs(
        {cid: fp for cid, fp in prev_inputs.items() if scope is None or cid in scope}, inputs
    )
    affected = [*diff.new, *diff.changed, *diff.removed]
    if not affected and previous and not rebuild:
        logger.info(f"Entities: {len(inputs)} claims unchanged; nothing to do")
        return {"claims_processed": 0, **entity_counts(state), "backup": None}

    analysis = build_or_load_analysis(
        claims, analyzer or DictaAnalyzer(), cache_path=analysis_path, source_path=resolved
    )
    clusterer = EntityClusterer(
        analysis,
        similarity_threshold=ENTITIES_CONFIG["similarity_threshold"],
        seed_path=seed_path,
    )
    corpus = ClaimCorpus(claims, analysis, original_by_id)
    stats = EntityUpdate(state, corpus, clusterer).apply(affected)
    stats.update(
        new_claims=len(diff.new), changed_claims=len(diff.changed), removed_claims=len(diff.removed)
    )

    out_inputs = {
        cid: fp for cid, fp in prev_inputs.items() if scope is not None and cid not in scope
    }
    out_inputs.update(inputs)
    state["metadata"] = _metadata(state, out_inputs, resolved, stats)
    backup = guarded_write(artifact.edited, state, backup=True)
    guarded_write(artifact.original, state, backup=False)
    meta = state["metadata"]
    logger.info(
        f"Entities: {meta['entity_count']} entities ({meta['multi_member_count']} multi-member, "
        f"{meta['ambiguous_count']} ambiguous) from {meta['distinct_entity_count']} strings; "
        f"run {stats}; commit {meta['git_commit']}; params {meta['params']}"
    )
    return {**stats, **entity_counts(state), "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 4: canonical entities (incremental).")
    parser.add_argument("--rebuild", action="store_true", help="re-cluster from scratch")
    parser.add_argument("--only", nargs="+", metavar="CLAIM_ID", help="restrict to these claims")
    args = parser.parse_args(argv)
    run(only=args.only, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
