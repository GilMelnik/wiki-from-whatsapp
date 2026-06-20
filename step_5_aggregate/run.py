"""Step 5: aggregate per-thread claims into per-topic knowledge.

For each topic the claims are grouped, near-duplicates merged with DBSCAN
(via cached E5 query/passage embeddings when available, otherwise a fuzzy text
fallback), distinct supporters tallied across threads (message authors and
reaction senders with positive reactions, each user counted once using the PRIVATE audit map),
contradicting stances per entity surfaced, and a month-by-month timeline built.

Claim embeddings and the all-claims distance matrix are cached under ``data/``
and rebuilt only when the claims source changes.

Output: ``data/claims_aggregated.json`` (no sender ids; counts only).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from utils.json_io import write_json_file
from utils.paths import resolve_claims_path
from utils.support import aggregate_reaction_summary, positive_reaction_senders_from_messages
from utils.taxonomy import category_title, get_page

DEFAULT_CLAIMS_PATH: Path | None = None
DEFAULT_AUDIT_PATH = Path("data/audit/claims_audit.json")
DEFAULT_OUTPUT_PATH = Path("data/claims_aggregated.json")
DEFAULT_CLAIM_QUERY_EMBEDDINGS_PATH = Path("data/claim_query_embeddings.json")
DEFAULT_CLAIM_PASSAGE_EMBEDDINGS_PATH = Path("data/claim_passage_embeddings.json")
DEFAULT_DISTANCE_MATRIX_PATH = Path("data/claim_distance_matrix.npy")
DEFAULT_DISTANCE_META_PATH = Path("data/claim_distance_matrix.json")
DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _load_audit_records(audit_path: Path | str) -> dict[str, dict[str, Any]]:
    """claim_id -> private audit record (supporters, reactions)."""

    path = Path(audit_path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        audit = json.load(f)
    return {rec["claim_id"]: rec for rec in audit["audit"]}


def _supporters_from_audit(record: dict[str, Any]) -> set[str]:
    """Distinct users supporting a claim (statements + positive reactions, deduped)."""

    statement_supporters = set(record.get("supporting_senders") or [])
    message_reactions = record.get("message_reactions")
    if message_reactions is not None:
        return statement_supporters | positive_reaction_senders_from_messages(
            message_reactions
        )
    if record.get("all_supporters"):
        return set(record["all_supporters"])
    supporters = set(statement_supporters)
    supporters.update(record.get("reaction_senders") or [])
    return supporters


def _claim_ids(claims: list[dict[str, Any]]) -> list[str]:
    return [c["claim_id"] for c in claims]


def _claim_texts(claims: list[dict[str, Any]]) -> list[str]:
    return [_normalize(c["claim_text"]) for c in claims]


def _claim_embeddings_need_rebuild(
    path: Path,
    *,
    source_path: Path,
    model_name: str,
    claims: list[dict[str, Any]],
    embedding_kind: str,
) -> bool:
    from step_1_threads_split.embedding.embedding import (
        MessageEmbeddings,
        _embedding_kind,
        _source_matches,
    )

    if not path.exists():
        return True
    meta = MessageEmbeddings.load(path).metadata
    if _embedding_kind(meta, default=embedding_kind) != embedding_kind:
        return True
    if meta.get("embedding_model") != model_name:
        return True
    if meta.get("claim_count") != len(claims):
        return True
    if meta.get("claim_ids") != _claim_ids(claims):
        return True
    return not _source_matches(meta, source_path)


def _write_claim_embeddings(
    output_path: Path,
    vectors: list[np.ndarray],
    *,
    source_path: Path,
    model_name: str,
    embedding_dim: int,
    embedding_kind: str,
    claims: list[dict[str, Any]],
    companion_path: Path | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "source": str(source_path.resolve()),
        "claim_count": len(claims),
        "claim_ids": _claim_ids(claims),
        "embedding_model": model_name,
        "embedding_dim": embedding_dim,
        "embedding_kind": embedding_kind,
    }
    if companion_path is not None:
        metadata["companion_path"] = str(companion_path)
    write_json_file(
        {"metadata": metadata, "embeddings": [v.tolist() for v in vectors]},
        output_path,
    )


def ensure_claim_embeddings(
    claims: list[dict[str, Any]],
    texts: list[str],
    source_path: Path | str,
    *,
    query_path: Path | str = DEFAULT_CLAIM_QUERY_EMBEDDINGS_PATH,
    passage_path: Path | str = DEFAULT_CLAIM_PASSAGE_EMBEDDINGS_PATH,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Build or load cached query/passage embeddings for all claims."""

    from step_1_threads_split.embedding.embedding import MessageEmbeddings

    source = Path(source_path).resolve()
    query_output = Path(query_path)
    passage_output = Path(passage_path)

    rebuild_passage = _claim_embeddings_need_rebuild(
        passage_output,
        source_path=source,
        model_name=model_name,
        claims=claims,
        embedding_kind="passage",
    )
    rebuild_query = _claim_embeddings_need_rebuild(
        query_output,
        source_path=source,
        model_name=model_name,
        claims=claims,
        embedding_kind="query",
    )

    embedder = None
    if rebuild_passage or rebuild_query:
        from step_1_threads_split.embedding.embedding import Embedder

        embedder = Embedder(model_name=model_name)

    if rebuild_passage:
        assert embedder is not None
        passage_vectors = embedder.encode_messages(texts)
        _write_claim_embeddings(
            passage_output,
            passage_vectors,
            source_path=source,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="passage",
            claims=claims,
            companion_path=query_output,
        )
    else:
        passage_vectors = MessageEmbeddings.load(passage_output).as_list()

    if rebuild_query:
        assert embedder is not None
        query_vectors = embedder.encode_queries(texts)
        _write_claim_embeddings(
            query_output,
            query_vectors,
            source_path=source,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="query",
            claims=claims,
            companion_path=passage_output,
        )
    else:
        query_vectors = MessageEmbeddings.load(query_output).as_list()

    claim_count = len(claims)
    if len(passage_vectors) != claim_count or len(query_vectors) != claim_count:
        raise ValueError(
            f"Embedding count mismatch for {source}: "
            f"claims={claim_count}, passage={len(passage_vectors)}, query={len(query_vectors)}"
        )
    return query_vectors, passage_vectors


def _distance_matrix_metadata(
    source_path: Path,
    claims: list[dict[str, Any]],
    *,
    distance_method: str,
) -> dict[str, Any]:
    return {
        "source": str(source_path.resolve()),
        "claim_count": len(claims),
        "claim_ids": _claim_ids(claims),
        "distance_method": distance_method,
    }


def _distance_matrix_need_rebuild(
    meta_path: Path,
    matrix_path: Path,
    expected: dict[str, Any],
) -> bool:
    if not meta_path.exists() or not matrix_path.exists():
        return True
    with meta_path.open(encoding="utf-8") as f:
        stored = json.load(f).get("metadata", {})
    return stored != expected


def ensure_distance_matrix(
    claims: list[dict[str, Any]],
    texts: list[str],
    source_path: Path | str,
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
    *,
    matrix_path: Path | str = DEFAULT_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = DEFAULT_DISTANCE_META_PATH,
) -> np.ndarray:
    """Build or load the cached all-claims distance matrix."""

    source = Path(source_path).resolve()
    matrix_output = Path(matrix_path)
    meta_output = Path(meta_path)
    distance_method = "embeddings" if query_vectors is not None else "fuzzy"
    expected_meta = _distance_matrix_metadata(
        source, claims, distance_method=distance_method
    )

    if not _distance_matrix_need_rebuild(meta_output, matrix_output, expected_meta):
        return np.load(matrix_output)

    dist = _claim_distance_matrix(texts, query_vectors, passage_vectors)
    matrix_output.parent.mkdir(parents=True, exist_ok=True)
    np.save(matrix_output, dist)
    write_json_file({"metadata": expected_meta}, meta_output)
    return dist


class _Embedder:
    """Tracks whether embedding-based merge is available."""

    def __init__(self, use_embeddings: bool):
        self.use_embeddings = use_embeddings
        self._failed = False

    def load(
        self,
        claims: list[dict[str, Any]],
        texts: list[str],
        source_path: Path,
    ) -> tuple[list[np.ndarray], list[np.ndarray]] | None:
        if not self.use_embeddings or self._failed:
            return None
        try:
            return ensure_claim_embeddings(claims, texts, source_path)
        except Exception:  # noqa: BLE001 - fall back to fuzzy
            self._failed = True
            return None


def _claim_similarity(
    i: int,
    j: int,
    *,
    texts: list[str],
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
) -> float:
    if query_vectors is not None and passage_vectors is not None:
        from step_1_threads_split.embedding.embedding import cosine_similarity

        sim_ij = cosine_similarity(query_vectors[i], passage_vectors[j])
        sim_ji = cosine_similarity(query_vectors[j], passage_vectors[i])
        return max(sim_ij, sim_ji)
    return SequenceMatcher(None, texts[i], texts[j]).ratio()


def _claim_distance_matrix(
    texts: list[str],
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
) -> np.ndarray:
    n = len(texts)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            d = 1.0 - _claim_similarity(
                i, j, texts=texts, query_vectors=query_vectors, passage_vectors=passage_vectors
            )
            dist[i, j] = dist[j, i] = d
    return dist


def _cluster_diameter(members: list[int], dist: np.ndarray) -> float:
    """Largest pairwise distance within the member set (complete-linkage diameter)."""

    if len(members) < 2:
        return 0.0
    return max(dist[i, j] for i in members for j in members if i < j)


def _split_oversized(
    members: list[int],
    dist: np.ndarray,
    *,
    max_size: int,
    keep_together_distance: float,
) -> list[list[int]]:
    """Recursively bisect a cluster until each piece is <= max_size.

    ponytail: soft cap, not hard. An oversized cluster whose complete-linkage
    diameter is already below ``keep_together_distance`` (near-duplicates) stays
    intact instead of being split. Worst case is O(n^2) per oversized cluster
    from the diameter scan; per-topic cluster sizes make that fine. If clusters
    ever get large, switch the diameter scan to the cached matrix max.
    """

    if len(members) <= max_size:
        return [members]
    if _cluster_diameter(members, dist) <= keep_together_distance:
        return [members]

    from sklearn.cluster import AgglomerativeClustering

    sub = dist[np.ix_(members, members)]
    halves = AgglomerativeClustering(
        n_clusters=2, metric="precomputed", linkage="complete"
    ).fit_predict(sub)
    out: list[list[int]] = []
    for label in (0, 1):
        half = [members[k] for k, lab in enumerate(halves) if lab == label]
        out.extend(
            _split_oversized(
                half,
                dist,
                max_size=max_size,
                keep_together_distance=keep_together_distance,
            )
        )
    return out


def _cluster(
    dist: np.ndarray,
    *,
    distance_threshold: float,
    max_size: int,
    keep_together_distance: float,
) -> list[int]:
    """Complete-linkage clusters bounded by diameter, then capped by size.

    Members within ``distance_threshold`` of each other group together (no
    DBSCAN-style chaining); oversized groups are bisected via _split_oversized.
    """

    from sklearn.cluster import AgglomerativeClustering

    n = dist.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]

    base = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="precomputed",
        linkage="complete",
    ).fit_predict(dist)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(base.tolist()):
        groups[label].append(idx)

    labels = [0] * n
    next_id = 0
    for members in groups.values():
        for piece in _split_oversized(
            members,
            dist,
            max_size=max_size,
            keep_together_distance=keep_together_distance,
        ):
            for idx in piece:
                labels[idx] = next_id
            next_id += 1
    return labels


def _medoid_index(members: list[int], dist: np.ndarray) -> int:
    """Index of the member with the smallest total distance to all others."""

    if len(members) == 1:
        return members[0]
    best = members[0]
    best_sum = float("inf")
    for i in members:
        total = sum(dist[i, j] for j in members if j != i)
        if total < best_sum:
            best_sum = total
            best = i
    return best


def build_merged_claim(
    member_claims: list[dict[str, Any]],
    audit_by_id: dict[str, dict[str, Any]],
    *,
    claim_text: str | None = None,
) -> dict[str, Any]:
    """Aggregate source claims into one merged cluster entry."""

    all_supporters: set[str] = set()
    statement_supporters: set[str] = set()
    reaction_supporters: set[str] = set()
    message_reactions: list[dict[str, Any]] = []
    for claim in member_claims:
        audit = audit_by_id.get(claim["claim_id"], {})
        all_supporters.update(_supporters_from_audit(audit))
        statement_supporters.update(audit.get("supporting_senders") or [])
        message_rx = audit.get("message_reactions")
        if message_rx is not None:
            reaction_supporters.update(
                positive_reaction_senders_from_messages(message_rx)
            )
        else:
            reaction_supporters.update(audit.get("reaction_senders") or [])
        message_reactions.extend(audit.get("message_reactions") or [])

    support_count = len(all_supporters) if all_supporters else sum(
        c.get("support_count", 1) for c in member_claims
    )
    reaction_only_count = len(reaction_supporters - statement_supporters)

    stances = Counter(c.get("stance", "neutral") for c in member_claims)
    dates = sorted(c["date"] for c in member_claims if c.get("date"))
    entities = sorted({e for c in member_claims for e in c.get("entities", [])})
    pii_redactions: list[dict[str, str]] = []
    for claim in member_claims:
        pii_redactions.extend(claim.get("_redactions") or [])

    if claim_text is None:
        claim_text = member_claims[0]["claim_text"]

    merged_claim: dict[str, Any] = {
        "claim_text": claim_text,
        "variants": [c["claim_text"] for c in member_claims],
        "stance": stances.most_common(1)[0][0],
        "stance_breakdown": dict(stances),
        "support_count": support_count,
        "statement_count": len(statement_supporters),
        "reaction_endorser_count": len(reaction_supporters),
        "reaction_only_count": reaction_only_count,
        "reaction_summary": aggregate_reaction_summary(message_reactions),
        "endorsement_count": len(member_claims),
        "thread_count": len({c["thread_id"] for c in member_claims}),
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "entities": entities,
        "source_claim_ids": [c["claim_id"] for c in member_claims],
    }
    if pii_redactions:
        merged_claim["pii_redactions"] = pii_redactions
        merged_claim["pii_needs_review"] = True
    return merged_claim


def _merge_claims(
    claims: list[dict[str, Any]],
    audit_by_id: dict[str, dict[str, Any]],
    dist: np.ndarray,
    similarity_threshold: float,
    *,
    max_cluster_size: int = 8,
    keep_together_similarity: float = 0.97,
) -> list[dict[str, Any]]:
    """Cluster same-stance near-duplicate claims and aggregate their support.

    Claims are partitioned by stance first so opposite-sentiment claims about
    the same entity never merge, then each stance group is clustered with
    complete-linkage agglomerative clustering under a soft size cap.
    """

    by_stance: dict[str, list[int]] = defaultdict(list)
    for idx, claim in enumerate(claims):
        by_stance[claim.get("stance", "neutral")].append(idx)

    merged: list[dict[str, Any]] = []
    for group in by_stance.values():
        sub = dist[np.ix_(group, group)]
        local_labels = _cluster(
            sub,
            distance_threshold=1.0 - similarity_threshold,
            max_size=max_cluster_size,
            keep_together_distance=1.0 - keep_together_similarity,
        )
        clusters: dict[int, list[int]] = defaultdict(list)
        for local_idx, label in enumerate(local_labels):
            clusters[label].append(group[local_idx])

        for members in clusters.values():
            member_claims = [claims[m] for m in members]
            medoid_idx = _medoid_index(members, dist)
            representative = claims[medoid_idx]
            merged.append(
                build_merged_claim(
                    member_claims,
                    audit_by_id,
                    claim_text=representative["claim_text"],
                )
            )

    merged.sort(key=lambda m: m["support_count"], reverse=True)
    return merged


def _entity_stances(merged: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for claim in merged:
        for entity in claim["entities"]:
            table[entity][claim["stance"]] += claim["support_count"]
    return {e: dict(s) for e, s in table.items()}


def _contradictions(entity_stances: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entity, stances in entity_stances.items():
        pos = stances.get("positive", 0)
        neg = stances.get("negative", 0)
        if pos > 0 and neg > 0:
            out.append({"entity": entity, "positive": pos, "negative": neg})
    out.sort(key=lambda d: d["positive"] + d["negative"], reverse=True)
    return out


def run(
    claims_path: Path | str | None = DEFAULT_CLAIMS_PATH,
    audit_path: Path | str = DEFAULT_AUDIT_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    use_embeddings: bool = True,
    similarity_threshold: float = 0.86,
    max_cluster_size: int = 8,
    keep_together_similarity: float = 0.97,
) -> dict[str, Any]:
    resolved_claims = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved_claims.open(encoding="utf-8") as f:
        claims_payload = json.load(f)
    claims = claims_payload["claims"]

    from step_4b_entities.run import apply_entity_resolution, load_entity_resolver

    entity_resolver = load_entity_resolver()
    apply_entity_resolution(claims, entity_resolver)

    audit_by_id = _load_audit_records(audit_path)
    embedder = _Embedder(use_embeddings)
    texts = _claim_texts(claims)
    claim_index = {c["claim_id"]: i for i, c in enumerate(claims)}

    embeddings = embedder.load(claims, texts, resolved_claims)
    query_vectors = passage_vectors = None
    if embeddings is not None:
        query_vectors, passage_vectors = embeddings

    dist = ensure_distance_matrix(
        claims,
        texts,
        resolved_claims,
        query_vectors,
        passage_vectors,
    )

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for tag in claim.get("topic_tags") or ["overview"]:
            by_topic[tag].append(claim)

    topics_out: dict[str, Any] = {}
    for topic_id, topic_claims in by_topic.items():
        page = get_page(topic_id)
        indices = [claim_index[c["claim_id"]] for c in topic_claims]
        idx = np.array(indices, dtype=int)
        topic_dist = dist[np.ix_(idx, idx)]
        merged = _merge_claims(
            topic_claims,
            audit_by_id,
            topic_dist,
            similarity_threshold,
            max_cluster_size=max_cluster_size,
            keep_together_similarity=keep_together_similarity,
        )
        entity_stances = _entity_stances(merged)
        timeline = Counter(
            c["date"] for c in topic_claims if c.get("date")
        )
        all_dates = sorted(c["date"] for c in topic_claims if c.get("date"))

        topics_out[topic_id] = {
            "title": page.title_he if page else topic_id,
            "category": page.category if page else "emergent",
            "category_title": category_title(page.category) if page else category_title("emergent"),
            "is_emergent": page is None,
            "claim_count": len(topic_claims),
            "merged_claim_count": len(merged),
            "merged_claims": merged,
            "entity_stances": entity_stances,
            "contradictions": _contradictions(entity_stances),
            "timeline": dict(sorted(timeline.items())),
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else [None, None],
        }

    output = {
        "topics": topics_out,
        "entities_registry": entity_resolver.registry() if entity_resolver else {},
        "metadata": {
            "source": str(resolved_claims),
            "entity_resolution": entity_resolver is not None,
            "topic_count": len(topics_out),
            "total_claims": len(claims),
            "merge_method": (
                "agglomerative_embeddings"
                if (use_embeddings and not embedder._failed)
                else "agglomerative_fuzzy"
            ),
            "similarity_threshold": similarity_threshold,
            "max_cluster_size": max_cluster_size,
            "keep_together_similarity": keep_together_similarity,
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


if __name__ == "__main__":
    run(similarity_threshold=0.90, max_cluster_size=8, keep_together_similarity=0.95)
