"""Step 5: aggregate per-thread claims into per-topic knowledge.

For each topic the claims are grouped, near-duplicates merged with DBSCAN
(via E5 query/passage embeddings when available, otherwise a fuzzy text
fallback), distinct supporters tallied across threads (message authors and
reaction senders with positive reactions, each user counted once using the PRIVATE audit map),
contradicting stances per entity surfaced, and a month-by-month timeline built.

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


class _Embedder:
    """Lazy wrapper that embeds claim texts, falling back to fuzzy matching."""

    def __init__(self, use_embeddings: bool):
        self.use_embeddings = use_embeddings
        self._embedder = None
        self._failed = False

    def embed(self, texts: list[str]):
        """Return (query_vectors, passage_vectors) for E5-style cross similarity."""
        if not self.use_embeddings or self._failed:
            return None
        try:
            if self._embedder is None:
                from step_1_threads_split.embedding.embedding import Embedder
                self._embedder = Embedder()
            return (
                self._embedder.encode_queries(texts),
                self._embedder.encode_messages(texts),
            )
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


def _dbscan(dist: np.ndarray, *, eps: float, min_samples: int) -> list[int]:
    """Cluster with sklearn DBSCAN; noise points each get their own label."""

    from sklearn.cluster import DBSCAN

    n = dist.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]

    raw = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed").fit_predict(dist)
    labels = raw.tolist()
    next_id = max(labels) + 1
    for i, label in enumerate(labels):
        if label == -1:
            labels[i] = next_id
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


def _merge_claims(
    claims: list[dict[str, Any]],
    audit_by_id: dict[str, dict[str, Any]],
    embedder: _Embedder,
    similarity_threshold: float,
) -> list[dict[str, Any]]:
    """DBSCAN-cluster near-duplicate claims and aggregate their support."""

    texts = [_normalize(c["claim_text"]) for c in claims]
    embeddings = embedder.embed(texts)
    query_vectors = passage_vectors = None
    if embeddings is not None:
        query_vectors, passage_vectors = embeddings

    dist = _claim_distance_matrix(texts, query_vectors, passage_vectors)
    labels = _dbscan(
        dist,
        eps=1.0 - similarity_threshold,
        min_samples=2,
    )
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        clusters[label].append(idx)

    merged: list[dict[str, Any]] = []
    for members in clusters.values():
        member_claims = [claims[m] for m in members]
        # Distinct supporters across the whole cluster (private audit -> count only).
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
        endorsement_count = len(member_claims)

        stances = Counter(c.get("stance", "neutral") for c in member_claims)
        dates = sorted(c["date"] for c in member_claims if c.get("date"))
        entities = sorted({e for c in member_claims for e in c.get("entities", [])})
        pii_redactions: list[dict[str, str]] = []
        for claim in member_claims:
            pii_redactions.extend(claim.get("_redactions") or [])
        pii_needs_review = bool(pii_redactions)
        medoid_idx = _medoid_index(members, dist)
        representative = claims[medoid_idx]

        merged_claim: dict[str, Any] = {
            "claim_text": representative["claim_text"],
            "variants": [c["claim_text"] for c in member_claims],
            "stance": stances.most_common(1)[0][0],
            "stance_breakdown": dict(stances),
            "support_count": support_count,
            "statement_count": len(statement_supporters),
            "reaction_endorser_count": len(reaction_supporters),
            "reaction_only_count": reaction_only_count,
            "reaction_summary": aggregate_reaction_summary(message_reactions),
            "endorsement_count": endorsement_count,
            "thread_count": len({c["thread_id"] for c in member_claims}),
            "date_range": [dates[0], dates[-1]] if dates else [None, None],
            "entities": entities,
            "source_claim_ids": [c["claim_id"] for c in member_claims],
        }
        if pii_needs_review:
            merged_claim["pii_redactions"] = pii_redactions
            merged_claim["pii_needs_review"] = True
        merged.append(merged_claim)

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
) -> dict[str, Any]:
    resolved_claims = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved_claims.open(encoding="utf-8") as f:
        claims_payload = json.load(f)
    claims = claims_payload["claims"]
    audit_by_id = _load_audit_records(audit_path)
    embedder = _Embedder(use_embeddings)

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for tag in claim.get("topic_tags") or ["overview"]:
            by_topic[tag].append(claim)

    topics_out: dict[str, Any] = {}
    for topic_id, topic_claims in by_topic.items():
        page = get_page(topic_id)
        merged = _merge_claims(
            topic_claims, audit_by_id, embedder, similarity_threshold
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
        "metadata": {
            "source": str(resolved_claims),
            "topic_count": len(topics_out),
            "total_claims": len(claims),
            "merge_method": (
                "dbscan_embeddings"
                if (use_embeddings and not embedder._failed)
                else "dbscan_fuzzy"
            ),
            "similarity_threshold": similarity_threshold,
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


if __name__ == "__main__":
    run()