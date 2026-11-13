"""Measure step 5's grouping on the dev corpus (numbers only; nothing here is a pipeline step).

``python -m step_5_aggregate.evaluate [--incremental]``

References (read only):

- the old pipeline's reviewed aggregation (``aggregate.eval.backup_dir``: its
  ``claims_aggregated_edited.json`` groups with several ``source_claim_ids``, plus the manual
  ``claim_aggregations.json``), mapped to current claim ids through the claims file's
  ``metadata.legacy_claim_ids``. Its pairs give a *recall*: how many of the pairs a human
  accepted are grouped again. It was built inside per-subject buckets and is almost all
  singletons, so it says little about precision;
- a private file of hand-labelled claim pairs (``aggregate.eval.pair_labels`` under the data
  dir: ``{"pairs": [{a, b, label}]}``, label ``same|related|contra|different``), sampled by
  cosine band and entity relation. Each band's share of ``same`` (and of the harmful
  ``contra`` / ``different``) estimates the *precision* of the pairs a setting groups.

For every setting of ``aggregate.eval.settings`` the full corpus is grouped from scratch (no
must-links, so the reference is not handed to it) and reported. ``--incremental`` runs the
80 % / 20 % experiment in ``AGGREGATE_EVAL_DIR`` (see ``step_5_aggregate.experiment``).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from step_5_aggregate.experiment import Pair, incremental_experiment, pair_of, pairs_of
from step_5_aggregate.groups import group_from_scratch
from step_5_aggregate.inputs import StepInputs, load_inputs
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer
from utils.config import DATA_DIR
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import STEP_5

EVAL_CONFIG = AGGREGATE_CONFIG["eval"]
logger = logging.getLogger(STEP_5)
HARMFUL = ("contra", "different")


class _NoNumberGuard(ClaimScorer):
    """The scorer without the number rule (the "embedding only" baseline)."""

    def number_clash(self, a: int, b: int) -> bool:
        return False


def reference_pairs(inputs: StepInputs, backup_dir: Path) -> set[Pair]:
    """Claim pairs the old reviewed aggregation (and its manual groupings) put together."""

    def mapped(ids: Iterable[str]) -> list[str]:
        return sorted({inputs.legacy_ids[c] for c in ids if c in inputs.legacy_ids})

    old = json.loads((backup_dir / STEP_5 / "claims_aggregated_edited.json").read_text("utf-8"))
    groups = [
        mapped(merged["source_claim_ids"])
        for bucket in old["topics"].values()
        for merged in bucket["merged_claims"]
    ]
    manual = json.loads(
        (backup_dir / "step_4_entities" / "claim_aggregations.json").read_text("utf-8")
    )
    groups += [mapped(g["claim_ids"]) for g in manual.get("aggregations") or []]
    return pairs_of(groups)


class PairJudge:
    """Precision estimates from hand-labelled pairs, per cosine band and entity relation."""

    def __init__(self, scorer: ClaimScorer, labels: list[dict[str, str]]) -> None:
        self.scorer = scorer
        self.bands = [float(b) for b in EVAL_CONFIG["bands"]]
        self.labels = {pair_of(p["a"], p["b"]): p["label"] for p in labels}
        self.by_cell: dict[tuple[int, str], Counter[str]] = {}
        for pair, label in self.labels.items():
            if all(c in scorer.row for c in pair):
                self.by_cell.setdefault(self.cell(pair), Counter())[label] += 1

    def cell(self, pair: Pair) -> tuple[int, str]:
        a, b = (self.scorer.row[c] for c in pair)
        cos = float(self.scorer.vectors[a] @ self.scorer.vectors[b])
        band = sum(1 for edge in self.bands if cos >= edge)
        if self.scorer.number_clash(a, b):
            relation = "numbers"
        elif self.scorer.shares_entity(a, b):
            relation = "shared"
        elif self.scorer.entities[a] and self.scorer.entities[b]:
            relation = "disjoint"
        else:
            relation = "none"
        return band, relation

    def estimate(self, pairs: Iterable[Pair]) -> dict[str, Any]:
        """Expected share of ``same`` / harmful pairs; pairs in unlabelled cells are skipped."""

        same = harmful = covered = total = 0.0
        for pair in pairs:
            total += 1
            counts = self.by_cell.get(self.cell(pair))
            if not counts:
                continue
            n = sum(counts.values())
            covered += 1
            same += counts["same"] / n
            harmful += sum(counts[k] for k in HARMFUL) / n
        return {
            "est_precision": round(same / covered, 3) if covered else None,
            "est_harmful": round(harmful / covered, 3) if covered else None,
            "pairs_covered": int(covered),
            "pairs": int(total),
        }

    def labelled(self, pairs: set[Pair]) -> dict[str, int]:
        return dict(Counter(label for pair, label in self.labels.items() if pair in pairs))

    def table(self) -> dict[str, dict[str, int]]:
        return {
            f"{self.band_name(band)} {relation}": dict(counts)
            for (band, relation), counts in sorted(self.by_cell.items())
        }

    def band_name(self, band: int) -> str:
        edges = [None, *self.bands, None]
        low, high = edges[band], edges[band + 1]
        return f"[{low or '-'}, {high or '-'})"


def measure_setting(
    setting: Mapping[str, Any],
    inputs: StepInputs,
    base: ClaimScorer,
    judge: PairJudge,
    reference: set[Pair],
) -> tuple[dict[str, Any], set[Pair]]:
    scorer_class = ClaimScorer if setting["number_guard"] else _NoNumberGuard
    scorer = scorer_class(
        inputs.claims,
        inputs.claim_entities,
        base.vectors,
        merge_threshold={"shared_entity": setting["shared"], "no_shared_entity": setting["other"]},
    )
    groups = group_from_scratch(inputs.claims, scorer)
    pairs = pairs_of(groups)
    sizes = [len(g) for g in groups]
    report = {
        "setting": setting["name"],
        "groups": len(groups),
        "multi_member_groups": sum(1 for s in sizes if s > 1),
        "claims_in_multi": sum(s for s in sizes if s > 1),
        "largest": max(sizes),
        "reference_recall": round(len(pairs & reference) / len(reference), 3),
        **judge.estimate(pairs),
        "labelled_pairs_grouped": judge.labelled(pairs),
    }
    return report, pairs


def evaluate() -> list[dict[str, Any]]:
    inputs = load_inputs()
    scorer = ClaimScorer.embed(inputs.claims, inputs.claim_entities)
    labels_path = DATA_DIR / EVAL_CONFIG["pair_labels"]
    labels = json.loads(labels_path.read_text("utf-8"))["pairs"] if labels_path.is_file() else []
    judge = PairJudge(scorer, labels)
    reference = reference_pairs(inputs, Path(EVAL_CONFIG["backup_dir"]))
    logger.info(f"reference: {len(reference)} pairs; labelled pairs: {len(labels)}")
    logger.info(f"labelled cells: {judge.table()}")
    reports: list[dict[str, Any]] = []
    grouped: dict[str, set[Pair]] = {}
    shipped = AGGREGATE_CONFIG["merge_threshold"]
    settings = [
        *EVAL_CONFIG["settings"],
        {"name": "shipped", "shared": shipped["shared_entity"],
         "other": shipped["no_shared_entity"], "number_guard": True},
    ]
    for setting in settings:
        report, pairs = measure_setting(setting, inputs, scorer, judge, reference)
        grouped[setting["name"]] = pairs
        reports.append(report)
        logger.info(f"setting {report}")
    flat = f"embedding {shipped['no_shared_entity']}"
    if flat in grouped:
        added = grouped["shipped"] - grouped[flat]
        removed = grouped[flat] - grouped["shipped"]
        logger.info(
            f"entity signal vs '{flat}': adds {len(added)} pairs {judge.estimate(added)}, "
            f"removes {len(removed)} pairs {judge.estimate(removed)}"
        )
    return reports


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Measure step 5 grouping (numbers only).")
    parser.add_argument("--incremental", action="store_true", help="run the 80/20 experiment")
    args = parser.parse_args(argv)
    setup_step_logging(STEP_5)
    logger.info(
        f"evaluate | git commit {git_commit()} | args {vars(args)} | config {AGGREGATE_CONFIG}"
    )
    if args.incremental:
        logger.info(f"incremental experiment: {incremental_experiment()}")
    else:
        evaluate()


if __name__ == "__main__":
    main()
