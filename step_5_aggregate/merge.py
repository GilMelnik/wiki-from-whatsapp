"""Merge clustered claims into per-topic aggregate entries for step 5.

Same-stance near-duplicate claims are clustered (see :mod:`clustering`) and
collapsed into a single entry that tallies distinct supporters across threads
(message authors + positive-reaction senders, each counted once via the private
audit map), keeps a stance breakdown, and surfaces per-entity contradictions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from step_5_aggregate.clustering import _cluster, _medoid_index
from utils.support import (
    aggregate_reaction_summary,
    participants_from_audit,
    reaction_senders_from_messages,
)


def _supporters_from_audit(record: dict[str, Any]) -> set[str]:
    return participants_from_audit(record, side="supporter")


def build_merged_claim(
    member_claims: list[dict[str, Any]],
    audit_by_id: dict[str, dict[str, Any]],
    *,
    claim_text: str | None = None,
) -> dict[str, Any]:
    """Aggregate source claims into one merged cluster entry."""

    # Expand any reviewer-forced group so its folded-away members still count.
    expanded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for claim in member_claims:
        for member in claim.get("_manual_group") or [claim]:
            cid = member["claim_id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                expanded.append(member)
    member_claims = expanded

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
                reaction_senders_from_messages(message_rx, sentiment="positive")
            )
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
