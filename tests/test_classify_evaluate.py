"""Offline checks of the step-2 evaluation metrics and the fallback sample."""

from __future__ import annotations

import numpy as np

from step_2_classify.evaluate import EvalSet, Scored

LABELS = np.array([True, True, True, True, False, False])
SCORES = np.array([0.9, 0.8, 0.4, 0.2, 0.3, 0.1])


def test_counts_and_rates_at_a_threshold() -> None:
    at = Scored(LABELS, SCORES).at(0.35)
    assert (at["tp"], at["fp"], at["fn"], at["tn"]) == (3, 0, 1, 2)
    assert at["recall"] == 0.75 and at["negative_recall"] == 1.0
    assert at["balanced_accuracy"] == 0.875


def test_threshold_choices() -> None:
    scored = Scored(LABELS, SCORES)
    assert scored.threshold_for_recall(1.0) == 0.2
    assert scored.threshold_for_recall(0.75) == 0.4
    assert scored.best_f1_threshold() == 0.2
    assert scored.auc() == 0.875


def test_fallback_sample_keeps_every_negative_and_is_fixed() -> None:
    labels = np.array([True] * 20 + [False] * 3)
    data = EvalSet(
        threads=[{} for _ in labels],
        labels=labels,
        groups=np.array(["model"] * len(labels)),
        sizes=np.ones(len(labels), dtype=int),
        passes_heuristic=np.ones(len(labels), dtype=bool),
    )
    sample = data.fallback_sample(8, seed=0)
    assert len(sample) == 8 and set(np.flatnonzero(~labels)) <= set(sample)
    assert (sample == data.fallback_sample(8, seed=0)).all()


def test_credible_drops_only_failed_call_negatives() -> None:
    data = EvalSet(
        threads=[{}, {}, {}],
        labels=np.array([True, False, False]),
        groups=np.array(["failed_call", "failed_call", "model"]),
        sizes=np.ones(3, dtype=int),
        passes_heuristic=np.ones(3, dtype=bool),
    )
    assert data.credible.tolist() == [True, False, True]
