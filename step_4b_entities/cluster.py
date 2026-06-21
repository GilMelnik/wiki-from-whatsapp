"""Cluster distinct entity records into suggested canonical entities."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from step_4b_entities.constants import (
    DEFAULT_ENTITY_DISTANCE_MATRIX_PATH,
    DEFAULT_ENTITY_DISTANCE_META_PATH,
    DEFAULT_SEED_PATH,
    DISTANCE_METHOD,
    MIN_SKELETON_LEN,
    SIMILARITY_THRESHOLD,
)
from step_4b_entities.normalize import normalize_name
from step_4b_entities.pair_index import EntityPairIndex
from utils.json_io import write_json_file


def load_seed_entries(seed_path: Path | str = DEFAULT_SEED_PATH) -> list[dict[str, Any]]:
    """Curated must-link anchors; empty when no seed file is present."""

    path = Path(seed_path)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("entities") or []
    return data or []


def _seed_index(
    seed_entries: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """``normalized name -> seed_id`` and ``seed_id -> entry`` lookups."""

    norm_to_seed: dict[str, str] = {}
    seed_by_id: dict[str, dict[str, Any]] = {}
    for idx, entry in enumerate(seed_entries):
        seed_id = entry.get("id") or f"seed{idx:03d}"
        seed_by_id[seed_id] = entry
        names = [entry.get("canonical", ""), *(entry.get("aliases") or [])]
        for nm in names:
            norm = normalize_name(nm)
            if norm:
                norm_to_seed.setdefault(norm, seed_id)
    return norm_to_seed, seed_by_id


def _entity_distance_matrix_metadata(
    source_path: Path,
    names: list[str],
    *,
    signature: str = "",
) -> dict[str, Any]:
    return {
        "source": str(source_path.resolve()),
        "entity_count": len(names),
        "entity_names": names,
        "distance_method": DISTANCE_METHOD,
        "min_skeleton_len": MIN_SKELETON_LEN,
        "signal_signature": signature,
    }


def _entity_distance_matrix_need_rebuild(
    meta_path: Path,
    matrix_path: Path,
    expected: dict[str, Any],
) -> bool:
    if not meta_path.exists() or not matrix_path.exists():
        return True
    with meta_path.open(encoding="utf-8") as f:
        stored = json.load(f).get("metadata", {})
    return stored != expected


def ensure_entity_distance_matrix(
    entities_or_index: list[dict[str, Any]] | EntityPairIndex,
    source_path: Path | str,
    *,
    matrix_path: Path | str = DEFAULT_ENTITY_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = DEFAULT_ENTITY_DISTANCE_META_PATH,
    seed_groups: list[str | None] | None = None,
) -> np.ndarray:
    """Build or load the cached entity distance matrix."""

    if isinstance(entities_or_index, EntityPairIndex):
        index = entities_or_index
    else:
        index = EntityPairIndex(entities_or_index, seed_groups)

    source = Path(source_path).resolve()
    matrix_output = Path(matrix_path)
    meta_output = Path(meta_path)
    signature = index.signal_signature()
    expected_meta = _entity_distance_matrix_metadata(
        source, index.names(), signature=signature
    )

    if not _entity_distance_matrix_need_rebuild(
        meta_output, matrix_output, expected_meta
    ):
        return np.load(matrix_output)

    dist = index.distance_matrix()
    matrix_output.parent.mkdir(parents=True, exist_ok=True)
    np.save(matrix_output, dist)
    write_json_file({"metadata": expected_meta}, meta_output)
    return dist


def _cluster_labels(dist: np.ndarray, *, similarity_threshold: float) -> list[int]:
    n = dist.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering

    labels = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=1.0 - similarity_threshold,
        metric="precomputed",
        linkage="complete",
    ).fit_predict(dist)
    return labels.tolist()


def _cluster_cohesion(members: list[int], dist: np.ndarray) -> float:
    """Worst (largest) pairwise distance turned into a similarity score."""

    if len(members) < 2:
        return 1.0
    diameter = max(dist[i, j] for i in members for j in members if i < j)
    return round(1.0 - float(diameter), 4)


def _union_contacts(
    members: list[dict[str, Any]], key: str = "contacts"
) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {"email": set(), "phone": set(), "website": set()}
    for member in members:
        for kind, values in (member.get(key) or {}).items():
            out.setdefault(kind, set()).update(values)
    return {kind: sorted(values) for kind, values in out.items()}


def _empty_contacts() -> dict[str, list[str]]:
    return {"email": [], "phone": [], "website": []}


def build_entity(
    entity_id: str,
    member_entities: list[dict[str, Any]],
    *,
    seed_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one canonical-entity record from its member name records."""

    members = sorted(member_entities, key=lambda e: e["count"], reverse=True)
    member_names = {m["name"] for m in members}
    canonical_name = members[0]["name"]
    topics: set[str] = set()
    for member in members:
        topics.update(member.get("topics") or [])

    if seed_entry:
        seed_canonical = seed_entry.get("canonical")
        # Only adopt the seed name when it is one of the clustered members, so the
        # reviewer's recompute (which keys canonical off aliases) stays consistent.
        if seed_canonical in member_names:
            canonical_name = seed_canonical
        topics.update(seed_entry.get("topics") or [])

    confident = _union_contacts(members, "contacts")
    uncertain = _union_contacts(members, "contacts_uncertain")
    uncertain = {
        kind: sorted(set(uncertain[kind]) - set(confident.get(kind, [])))
        for kind in uncertain
    }

    entity: dict[str, Any] = {
        "entity_id": entity_id,
        "canonical_name": canonical_name,
        "status": "suggested",
        "aliases": [m["name"] for m in members],
        "members": [
            {
                "name": m["name"],
                "claim_ids": m.get("claim_ids"),
                "count": m["count"],
                "sample_claim_ids": m["sample_claim_ids"],
                "topics": m["topics"],
                "contacts": m["contacts"],
                "contacts_uncertain": m.get("contacts_uncertain") or _empty_contacts(),
            }
            for m in members
        ],
        "contacts": confident,
        "contacts_uncertain": uncertain,
        "topics": sorted(topics),
        "merge_signals": [],
        "score": 1.0,
    }
    if seed_entry and seed_entry.get("type"):
        entity["type"] = seed_entry["type"]
    return entity


def cluster_entities(
    entities: list[dict[str, Any]],
    source_path: Path | str,
    *,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    matrix_path: Path | str = DEFAULT_ENTITY_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = DEFAULT_ENTITY_DISTANCE_META_PATH,
    seed_path: Path | str = DEFAULT_SEED_PATH,
) -> list[dict[str, Any]]:
    """Group distinct entity records into suggested canonical entities."""

    norm_to_seed, seed_by_id = _seed_index(load_seed_entries(seed_path))
    seed_groups = [norm_to_seed.get(e["normalized"]) for e in entities]
    index = EntityPairIndex(entities, seed_groups)

    dist = ensure_entity_distance_matrix(
        index,
        source_path,
        matrix_path=matrix_path,
        meta_path=meta_path,
    )
    labels = _cluster_labels(dist, similarity_threshold=similarity_threshold)

    grouped: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        grouped[label].append(idx)

    # Which signals fired inside each cluster, and which near-identical short names
    # the topic guard held apart (surfaced for manual review as "ambiguous").
    cluster_signals: dict[int, set[str]] = defaultdict(set)
    guarded_pairs: list[tuple[int, int]] = []
    n = len(entities)
    for i in range(n):
        for j in range(i + 1, n):
            sim, signals, guarded = index.pair_signals(i, j)
            if labels[i] == labels[j] and sim >= similarity_threshold:
                cluster_signals[labels[i]].update(signals)
            if guarded and labels[i] != labels[j]:
                guarded_pairs.append((i, j))

    label_to_entity: dict[int, dict[str, Any]] = {}
    out_entities: list[dict[str, Any]] = []
    for n_idx, (label, indices) in enumerate(sorted(grouped.items())):
        seed_id = next(
            (seed_groups[i] for i in indices if seed_groups[i] is not None), None
        )
        entity = build_entity(
            f"e{n_idx:04d}",
            [entities[i] for i in indices],
            seed_entry=seed_by_id.get(seed_id) if seed_id else None,
        )
        entity["score"] = _cluster_cohesion(indices, dist)
        if len(indices) > 1:
            entity["merge_signals"] = sorted(cluster_signals.get(label, set()))
        label_to_entity[label] = entity
        out_entities.append(entity)

    # Homonym guard: each held-apart pair flags both entities as ambiguous and
    # records the conflicting canonical name for the reviewer.
    for i, j in guarded_pairs:
        ent_i = label_to_entity[labels[i]]
        ent_j = label_to_entity[labels[j]]
        for ent, other in ((ent_i, ent_j), (ent_j, ent_i)):
            ent["status"] = "ambiguous"
            conflicts = set(ent.get("conflict_with") or [])
            conflicts.add(other["canonical_name"])
            ent["conflict_with"] = sorted(conflicts)

    out_entities.sort(
        key=lambda e: sum(m["count"] for m in e["members"]), reverse=True
    )
    return out_entities
