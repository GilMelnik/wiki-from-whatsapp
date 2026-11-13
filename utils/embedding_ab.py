"""A/B test of embedding models on Hebrew claim pairs mined from a human-reviewed backup.

Positives are claim pairs a human judged to say the same thing: every pair inside a manual
aggregation group (``step_4_entities/claim_aggregations.json``) and every pair inside one
merged claim's ``source_claim_ids`` (``step_5_aggregate/claims_aggregated_edited.json``).
Negatives are random pairs of claims that share a topic tag but are not connected by any
positive pair. For each model the script reports ROC-AUC, the best-F1 cosine threshold,
precision at the recall target and the mean cosine of positives vs negatives.

Hosted models go through ``utils.embedder.Embedder`` (cached, so a re-run is free);
``--local`` adds sentence-transformers models as extra rows. The backup is only read.

    python -m utils.embedding_ab --models gemini-embedding-001 gemini-embedding-2 --pairs 500
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import precision_recall_curve, roc_auc_score

from utils.config import ROOT_CONFIG, section
from utils.embedder import Embedder, Kind
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import SHARED

logger = logging.getLogger(__name__)

Pair = tuple[str, str]
AB_KIND: Kind = "similarity"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def positive_groups(backup_dir: Path) -> list[list[str]]:
    """Claim-id groups a human judged equivalent (manual groups + multi-source merged claims)."""
    manual = _load_json(backup_dir / "step_4_entities" / "claim_aggregations.json")
    merged = _load_json(backup_dir / "step_5_aggregate" / "claims_aggregated_edited.json")
    groups = [g["claim_ids"] for g in manual["aggregations"]]
    for topic in merged["topics"].values():
        groups.extend(m["source_claim_ids"] for m in topic["merged_claims"])
    return [g for g in groups if len(g) > 1]


def build_pairs(
    claims: dict[str, dict[str, Any]], groups: Sequence[Sequence[str]], pairs: int, rng: random.Random
) -> tuple[list[Pair], list[Pair]]:
    """Sample up to ``pairs`` positive and ``pairs`` negative claim-id pairs."""
    known = [[cid for cid in g if cid in claims] for g in groups]
    skipped = sum(len(g) for g in groups) - sum(len(g) for g in known)
    if skipped:
        logger.warning(f"{skipped} grouped claim ids are missing from claims_edited.json")
    positives = sorted(
        {tuple(sorted(p)) for g in known for p in itertools.combinations(dict.fromkeys(g), 2)}
    )
    # Claims connected by any positive pair are the same claim: never use them as negatives.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in positives:
        parent[find(a)] = find(b)

    by_tag: dict[str, list[str]] = {}
    for cid, claim in claims.items():
        for tag in claim.get("topic_tags") or []:
            by_tag.setdefault(tag, []).append(cid)
    candidates = [tag for tag, ids in by_tag.items() if len(ids) > 1]
    negatives: set[Pair] = set()
    attempts = 0
    while len(negatives) < pairs and attempts < pairs * 50:
        attempts += 1
        a, b = rng.sample(by_tag[rng.choice(candidates)], 2)
        if a in parent and b in parent and find(a) == find(b):
            continue
        negatives.add(tuple(sorted((a, b))))
    if len(negatives) < pairs:
        logger.warning(f"only {len(negatives)} negative pairs found for {pairs} requested")
    sampled_positives = rng.sample(positives, min(pairs, len(positives)))
    return sampled_positives, sorted(negatives)


class LocalEmbedder:
    """A sentence-transformers model with the same ``embed_texts`` shape as ``Embedder``."""

    def __init__(self, model_id: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = model_id
        self._encoder = SentenceTransformer(model_id)
        prefixes = section("embedding")["ab"]["local_text_prefix"]
        self._prefix = next((p for key, p in prefixes.items() if key in model_id.lower()), "")

    def embed_texts(self, texts: Sequence[str], kind: Kind) -> np.ndarray:
        return self._encoder.encode(
            [self._prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True
        ).astype(np.float32)


def score_model(
    embedder: Embedder | LocalEmbedder,
    texts: dict[str, str],
    positives: Sequence[Pair],
    negatives: Sequence[Pair],
    recall_target: float,
) -> dict[str, float | str]:
    """Metrics of one model: cosine of each pair vs its positive/negative label."""
    ids = sorted({cid for pair in (*positives, *negatives) for cid in pair})
    vectors = embedder.embed_texts([texts[cid] for cid in ids], AB_KIND)
    row = {cid: i for i, cid in enumerate(ids)}
    pairs = [*positives, *negatives]
    cosines = np.array([float(vectors[row[a]] @ vectors[row[b]]) for a, b in pairs])
    labels = np.array([1] * len(positives) + [0] * len(negatives))
    precision, recall, thresholds = precision_recall_curve(labels, cosines)
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    best = int(np.argmax(f1))
    at_recall = precision[recall >= recall_target]
    return {
        "model": embedder.model,
        "roc_auc": float(roc_auc_score(labels, cosines)),
        "best_f1": float(f1[best]),
        "best_f1_threshold": float(thresholds[best]),
        "precision_at_recall": float(at_recall.max()) if at_recall.size else float("nan"),
        "mean_cos_pos": float(cosines[labels == 1].mean()),
        "mean_cos_neg": float(cosines[labels == 0].mean()),
    }


def markdown_table(rows: Sequence[dict[str, float | str]], recall_target: float) -> str:
    header = (
        f"| model | ROC-AUC | best F1 | F1 threshold | precision@{recall_target:.2f} recall "
        "| mean cos pos | mean cos neg |\n|---|---|---|---|---|---|---|"
    )
    lines = [
        f"| {r['model']} | {r['roc_auc']:.4f} | {r['best_f1']:.3f} | {r['best_f1_threshold']:.3f} "
        f"| {r['precision_at_recall']:.3f} | {r['mean_cos_pos']:.3f} | {r['mean_cos_neg']:.3f} |"
        for r in rows
    ]
    return "\n".join([header, *lines])


def parse_args() -> argparse.Namespace:
    cfg = section("embedding")["ab"]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=cfg["models"], help="Gemini embedding models")
    parser.add_argument("--local", nargs="*", default=[], help="sentence-transformers model ids")
    parser.add_argument("--pairs", type=int, default=cfg["pairs"], help="pairs per class")
    parser.add_argument("--seed", type=int, default=cfg["seed"])
    parser.add_argument("--backup-dir", type=Path, default=Path(cfg["backup_dir"]))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_step_logging(SHARED)
    random.seed(args.seed)
    np.random.seed(args.seed)
    rng = random.Random(args.seed)
    logger.info(f"embedding A/B commit={git_commit()} args={vars(args)} config={ROOT_CONFIG['embedding']}")

    claim_list = _load_json(args.backup_dir / "step_3_extract" / "claims_edited.json")["claims"]
    claims = {c["claim_id"]: c for c in claim_list}
    positives, negatives = build_pairs(claims, positive_groups(args.backup_dir), args.pairs, rng)
    logger.info(f"{len(positives)} positive and {len(negatives)} negative pairs")
    texts = {cid: _normalize(c["claim_text"]) for cid, c in claims.items()}

    recall_target = float(section("embedding")["ab"]["recall_target"])
    rows = [
        score_model(Embedder(model=name), texts, positives, negatives, recall_target)
        for name in args.models
    ]
    rows += [
        score_model(LocalEmbedder(model_id), texts, positives, negatives, recall_target)
        for model_id in args.local
    ]
    logger.info(f"A/B results ({len(positives)} pos / {len(negatives)} neg pairs):\n"
                f"{markdown_table(rows, recall_target)}")


if __name__ == "__main__":
    main()
