"""Measure step 2's classifier on the reviewed labels of the old pipeline (numbers only).

``python -m step_2_classify.evaluate [--languages he en] [--threshold T] [--no-fallback]``

Reads, never writes, the backup in ``classify.eval_backup_dir``: the threads of
``step_1_threads_split/threads_edited.json`` and their ``is_knowledge_bearing`` labels in
``step_2_classify/threads_classified_edited.json``. Every thread is scored by the configured
classifier once per question language (the heuristic is reported separately). The fallback
classifier (``classifier.fallback``) scores a fixed sample of ``classify.eval_fallback_sample``
threads: every negative plus random positives (``classify.eval_seed``), since negatives are the
scarce class and ROC-AUC / recall per class do not depend on the class mix.

Label provenance comes from the old records' ``reason`` (``classify.eval_reason_groups``,
matched as prefixes; anything else is ``model``). A negative whose old classifier call failed
(``failed_call``) was never judged, by a model or a person, so the "credible" slice leaves
those out. The markdown report is logged (console and the step-2 log file), together with the
HTTP statuses, retries, latency and Jev input tokens of the paid calls (cached answers are free
and not counted).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from step_1_threads_split.evaluate import _git_commit, _markdown_table
from step_2_classify.run import CLASSIFY_CONFIG, ThreadClassifier
from utils.config import DATA_DIR, section
from utils.llm_client import Classifier
from utils.logging_setup import setup_step_logging
from utils.paths import CLASSIFIED, STEP_2, THREADS
from utils.threads_io import load_threads

CLASSIFIER_CONFIG = section("classifier")
logger = logging.getLogger(STEP_2)
TOKENS_PATTERN = re.compile(r"input_tokens=(\d+)")


@dataclass(frozen=True)
class EvalSet:
    """The labelled threads, aligned by position with every score array."""

    threads: list[dict[str, Any]]
    labels: np.ndarray
    groups: np.ndarray
    sizes: np.ndarray
    passes_heuristic: np.ndarray

    @classmethod
    def load(cls, backup_dir: Path, heuristic: Callable[[dict[str, Any]], bool]) -> EvalSet:
        threads = load_threads(backup_dir / THREADS.edited.relative_to(DATA_DIR))["threads"]
        labels_path = backup_dir / CLASSIFIED.edited.relative_to(DATA_DIR)
        records = {r["thread_id"]: r for r in load_threads(labels_path)["threads"]}
        missing = [t["thread_id"] for t in threads if t["thread_id"] not in records]
        if missing:
            raise ValueError(f"{len(missing)} threads have no label in {labels_path}")
        reasons = [str(records[t["thread_id"]].get("reason", "")) for t in threads]
        return cls(
            threads=threads,
            labels=np.array([bool(records[t["thread_id"]]["is_knowledge_bearing"]) for t in threads]),
            groups=np.array([_reason_group(reason) for reason in reasons]),
            sizes=np.array([len(t["messages"]) for t in threads]),
            passes_heuristic=np.array([heuristic(t) for t in threads]),
        )

    @property
    def credible(self) -> np.ndarray:
        """Leave out negatives that are only a failed call of the old classifier."""
        return ~((self.groups == "failed_call") & ~self.labels)

    def fallback_sample(self, size: int, seed: int) -> np.ndarray:
        """Every negative, then random positives up to ``size`` (sorted positions)."""
        negatives = np.flatnonzero(~self.labels)
        positives = np.flatnonzero(self.labels)
        extra = max(0, size - len(negatives))
        chosen = np.random.default_rng(seed).choice(positives, size=extra, replace=False)
        return np.sort(np.concatenate([negatives, chosen]))[:size]


def _reason_group(reason: str) -> str:
    for group, prefix in CLASSIFY_CONFIG["eval_reason_groups"].items():
        if reason.startswith(prefix):
            return group
    return "model"


@dataclass(frozen=True)
class Scored:
    """Labels and P(yes) of one classifier on one slice."""

    labels: np.ndarray
    scores: np.ndarray

    def auc(self) -> float:
        return float(roc_auc_score(self.labels, self.scores))

    def average_precision(self) -> float:
        return float(average_precision_score(self.labels, self.scores))

    def auc_interval(self, rounds: int, seed: int) -> tuple[float, float]:
        """95% bootstrap interval of the ROC-AUC."""
        rng = np.random.default_rng(seed)
        values = []
        for _ in range(rounds):
            pick = rng.integers(0, len(self.labels), len(self.labels))
            if self.labels[pick].all() or not self.labels[pick].any():
                continue
            values.append(roc_auc_score(self.labels[pick], self.scores[pick]))
        low, high = np.percentile(values, [2.5, 97.5])
        return float(low), float(high)

    def at(self, threshold: float) -> dict[str, float]:
        predicted = self.scores >= threshold
        tp = int((predicted & self.labels).sum())
        fp = int((predicted & ~self.labels).sum())
        fn = int((~predicted & self.labels).sum())
        tn = int((~predicted & ~self.labels).sum())
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        negative_recall = tn / (tn + fp) if tn + fp else 1.0
        return {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "negative_recall": negative_recall,
            "balanced_accuracy": (recall + negative_recall) / 2,
            "accuracy": (tp + tn) / len(self.labels),
        }

    def best_f1_threshold(self) -> float:
        return max(np.unique(self.scores), key=lambda t: (self.at(t)["f1"], t))

    def threshold_for_recall(self, target: float) -> float:
        """The highest threshold whose recall on the positives is at least ``target``."""
        reaching = [t for t in np.unique(self.scores) if self.at(t)["recall"] >= target]
        return float(max(reaching))


class CallStats(logging.Handler):
    """Counts what the SDKs log per HTTP request, and keeps their per-request lines quiet.

    TypeSafe: ``typesafe_sdk`` logs every response (status, latency) and retry; the provider
    logs Jev's input tokens at DEBUG on the classifier logger. Anthropic: ``httpx`` logs every
    response. ``httpx2`` repeats TypeSafe's requests and is only silenced.
    """

    SDK_LOGGERS = {"typesafe_sdk": "typesafe", "httpx": "anthropic", "httpx2": None}

    def __init__(self, classifier_logger: logging.Logger) -> None:
        super().__init__(logging.DEBUG)
        self.classifier_logger = classifier_logger
        self.statuses: Counter[tuple[str, int]] = Counter()
        self.retries = 0
        self.connection_errors: Counter[str] = Counter()
        self.latencies_ms: list[float] = []
        self.input_tokens = 0

    def install(self) -> None:
        for name in (*self.SDK_LOGGERS, self.classifier_logger.name):
            sdk_logger = logging.getLogger(name)
            sdk_logger.addHandler(self)
            sdk_logger.propagate = False
        self.classifier_logger.setLevel(logging.DEBUG)

    def uninstall(self) -> None:
        for name in (*self.SDK_LOGGERS, self.classifier_logger.name):
            sdk_logger = logging.getLogger(name)
            sdk_logger.removeHandler(self)
            sdk_logger.propagate = True

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == self.classifier_logger.name:
            match = TOKENS_PATTERN.search(record.getMessage())
            if match:
                self.input_tokens += int(match.group(1))
            elif record.levelno >= logging.INFO:
                logger.handle(record)
            return
        provider = self.SDK_LOGGERS.get(record.name)
        args = record.args if isinstance(record.args, tuple) else ()
        if provider == "typesafe" and " retry " in record.msg:
            self.retries += 1
        elif provider == "typesafe" and " in " in record.msg and len(args) >= 4:
            self._response(provider, int(args[2]))
            self.latencies_ms.append(float(args[3]))
        elif provider == "typesafe" and len(args) == 3:
            self.connection_errors[str(args[2])] += 1
            logger.warning(f"typesafe connection error: {args[2]}")
        elif provider == "anthropic" and len(args) >= 4:
            self._response(provider, int(args[3]))

    def _response(self, provider: str, status: int) -> None:
        self.statuses[(provider, status)] += 1
        if status != 200:
            logger.warning(f"{provider} HTTP {status}")

    def report(self) -> str:
        latencies = np.array(self.latencies_ms) if self.latencies_ms else np.zeros(1)
        rows = [[provider, status, count] for (provider, status), count in sorted(self.statuses.items())]
        return "\n".join(
            [
                _markdown_table(["provider", "HTTP status", "responses"], rows or [["-", "-", 0]]),
                "",
                f"TypeSafe retries: {self.retries}; connection errors: "
                f"{dict(self.connection_errors) or 'none'}; latency per request: median "
                f"{np.median(latencies):.0f} ms, p95 {np.percentile(latencies, 95):.0f} ms, "
                f"max {latencies.max():.0f} ms; Jev input tokens (paid calls): {self.input_tokens}",
            ]
        )


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _summary_rows(name: str, scored: Scored, threshold: float, rounds: int, seed: int) -> list[Any]:
    low, high = scored.auc_interval(rounds, seed)
    at = scored.at(threshold)
    return [
        name, len(scored.labels), int(scored.labels.sum()), int((~scored.labels).sum()),
        f"{scored.auc():.3f} [{low:.3f}, {high:.3f}]", _fmt(scored.average_precision()),
        _fmt(at["precision"]), _fmt(at["recall"]), _fmt(at["f1"]),
        _fmt(at["balanced_accuracy"]), _fmt(at["negative_recall"]),
        f"{at['tp']}/{at['fp']}/{at['fn']}/{at['tn']}",
    ]


SUMMARY_HEADERS = [
    "slice", "n", "pos", "neg", "ROC-AUC [95% CI]", "avg precision", "precision", "recall", "F1",
    "balanced acc", "neg recall", "TP/FP/FN/TN",
]


def _sweep(scored: Scored, thresholds: Sequence[float]) -> str:
    rows = []
    for threshold in thresholds:
        at = scored.at(threshold)
        rows.append(
            [_fmt(threshold), _fmt(at["recall"]), _fmt(at["negative_recall"]), _fmt(at["precision"]),
             _fmt(at["f1"]), _fmt(at["balanced_accuracy"]), at["tp"] + at["fp"]]
        )
    headers = ["threshold", "recall", "neg recall", "precision", "F1", "balanced acc", "predicted pos"]
    return _markdown_table(headers, rows)


def _size_bucket(size: int) -> str:
    edges = CLASSIFY_CONFIG["eval_size_buckets"]
    lower = 1
    for edge in edges:
        if size < edge:
            return f"{lower}-{edge - 1}"
        lower = edge
    return f"{lower}+"


def _errors(data: EvalSet, scores: np.ndarray, mask: np.ndarray, threshold: float) -> str:
    """False negatives / positives by thread size and by label provenance (counts only)."""
    predicted = scores >= threshold
    rows = []
    buckets = [_size_bucket(size) for size in (1, *CLASSIFY_CONFIG["eval_size_buckets"])]
    keys = [
        ("messages", [_size_bucket(int(s)) for s in data.sizes], buckets),
        ("labelled by", list(data.groups), sorted(set(data.groups))),
    ]
    for dimension, values, order in keys:
        for value in order:
            sel = mask & (np.array(values) == value)
            pos = sel & data.labels
            neg = sel & ~data.labels
            fn = int((pos & ~predicted).sum())
            fp = int((neg & predicted).sum())
            rows.append([dimension, value, int(sel.sum()), int(pos.sum()), fn, int(neg.sum()), fp])
    errors = mask & (predicted != data.labels)
    sizes = {name: data.sizes[errors & want] for name, want in (("FN", data.labels), ("FP", ~data.labels))}
    lines = [_markdown_table(["by", "group", "n", "pos", "FN", "neg", "FP"], rows), ""]
    for name, values in sizes.items():
        if len(values):
            lines.append(
                f"{name}: {len(values)} threads, messages median {np.median(values):.0f}, "
                f"min {values.min()}, max {values.max()}"
            )
    return "\n".join(lines)


def build_report(
    data: EvalSet,
    jev: dict[str, np.ndarray],
    fallback: tuple[str, np.ndarray, np.ndarray] | None,
    threshold: float,
) -> str:
    rounds, seed = CLASSIFY_CONFIG["eval_bootstrap"], CLASSIFY_CONFIG["eval_seed"]
    credible = data.credible
    groups = Counter(zip(data.groups, data.labels))
    lines = [
        f"## Labels: {len(data.labels)} threads, {int(data.labels.sum())} positive, "
        f"{int((~data.labels).sum())} negative",
        "",
        "Provenance (group, label: count): "
        + ", ".join(f"{g} {'pos' if lab else 'neg'}: {n}" for (g, lab), n in sorted(groups.items())),
        f"Credible slice leaves out {int((~credible).sum())} failed-call negatives; heuristic "
        f"rejects {int((~data.passes_heuristic).sum())} threads "
        f"({int((~data.passes_heuristic & data.labels).sum())} labelled positive).",
        "",
        f"## Summary at threshold {threshold}",
        "",
    ]
    rows = []
    for name, scores in jev.items():
        rows.append(_summary_rows(f"{name} all", Scored(data.labels, scores), threshold, rounds, seed))
        rows.append(
            _summary_rows(
                f"{name} credible", Scored(data.labels[credible], scores[credible]), threshold, rounds, seed
            )
        )
        gated = np.where(data.passes_heuristic, scores, 0.0)
        rows.append(
            _summary_rows(
                f"{name} credible + heuristic",
                Scored(data.labels[credible], gated[credible]), threshold, rounds, seed,
            )
        )
    lines.append(_markdown_table(SUMMARY_HEADERS, rows))
    for name, scores in jev.items():
        scored = Scored(data.labels[credible], scores[credible])
        best = scored.best_f1_threshold()
        targets = {t: scored.threshold_for_recall(t) for t in CLASSIFY_CONFIG["eval_recall_targets"]}
        sweep = sorted({0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, best, threshold, *targets.values()})
        failed = scores[~credible]
        lines += [
            "",
            f"## {name}: threshold sweep (credible slice)",
            "",
            f"Best-F1 threshold {best:.3f}; highest threshold reaching recall "
            + ", ".join(f"{t}: {v:.3f}" for t, v in targets.items()),
            "",
            _sweep(scored, sweep),
            "",
            f"Failed-call negatives ({len(failed)}): {int((failed >= threshold).sum())} scored >= "
            f"{threshold}; deciles of P(yes): {np.round(np.percentile(failed, range(10, 100, 10)), 2).tolist()}"
            if len(failed) else "",
            f"P(yes) deciles, credible positives: "
            f"{np.round(np.percentile(scored.scores[scored.labels], range(10, 100, 10)), 2).tolist()}; "
            f"credible negatives: "
            f"{np.round(np.percentile(scored.scores[~scored.labels], range(10, 100, 10)), 2).tolist()}",
            "",
            f"## {name}: errors at threshold {threshold} (credible slice)",
            "",
            _errors(data, scores, credible, threshold),
        ]
    if fallback is not None:
        lines += ["", *_fallback_section(data, jev, fallback, threshold)]
    return "\n".join(lines)


def _fallback_section(
    data: EvalSet, jev: dict[str, np.ndarray], fallback: tuple[str, np.ndarray, np.ndarray],
    threshold: float,
) -> list[str]:
    name, sample, scores = fallback
    rounds, seed = CLASSIFY_CONFIG["eval_bootstrap"], CLASSIFY_CONFIG["eval_seed"]
    credible = data.credible[sample]
    labels = data.labels[sample]
    failed = ~credible
    rows = []
    agreement = []
    for label, values in [*((n, s[sample]) for n, s in jev.items()), (name, scores)]:
        rows.append(_summary_rows(label, Scored(labels[credible], values[credible]), threshold, rounds, seed))
        agreement.append(
            f"{label}: {int((values[failed] >= threshold).sum())} of {int(failed.sum())} "
            f"failed-call negatives scored >= {threshold}"
        )
    pairs = [
        f"{n} vs {name}: same decision on {float(((s[sample] >= threshold) == (scores >= threshold)).mean()):.3f}"
        for n, s in jev.items()
    ]
    return [
        f"## Fallback comparison on the fixed sample ({len(sample)} threads: every negative + random "
        f"positives), credible part",
        "",
        _markdown_table(SUMMARY_HEADERS, rows),
        "",
        "; ".join(agreement),
        "; ".join(pairs),
    ]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_2_classify.evaluate",
        description="Score the step-2 classifier against the backup's reviewed labels.",
    )
    parser.add_argument(
        "--languages", nargs="+", default=sorted(CLASSIFY_CONFIG["questions"]),
        help="question languages to score with the configured classifier (default: %(default)s)",
    )
    parser.add_argument(
        "--threshold", type=float, default=CLASSIFY_CONFIG["threshold"],
        help="decision threshold the tables report at (default: %(default)s)",
    )
    parser.add_argument(
        "--no-fallback", action="store_true", help="skip the fallback classifier's sample"
    )
    args = parser.parse_args(argv)

    setup_step_logging(STEP_2)
    seed = CLASSIFY_CONFIG["eval_seed"]
    random.seed(seed)
    np.random.seed(seed)
    logger.info(
        f"evaluate | git commit {_git_commit()} | args {vars(args)} | "
        f"config.classify {json.dumps(CLASSIFY_CONFIG, ensure_ascii=False)} | "
        f"config.classifier.stages {json.dumps(CLASSIFIER_CONFIG['stages'])}"
    )
    classifier_logger = logging.getLogger(f"{STEP_2}.classifier")
    deciders = {
        language: ThreadClassifier.from_config(classifier_logger, language=language)
        for language in args.languages
    }
    first = next(iter(deciders.values()))
    data = EvalSet.load(Path(CLASSIFY_CONFIG["eval_backup_dir"]), first.passes_heuristic)
    stats = CallStats(classifier_logger)
    stats.install()
    try:
        jev = {
            f"{d.classifier.model} {language}": np.array(d.score(data.threads))
            for language, d in deciders.items()
        }
        fallback = None
        if not args.no_fallback:
            settings = CLASSIFIER_CONFIG["fallback"]
            decider = ThreadClassifier.from_config(
                classifier_logger,
                classifier=Classifier(settings["provider"], settings["model"], logger=classifier_logger),
            )
            sample = data.fallback_sample(CLASSIFY_CONFIG["eval_fallback_sample"], seed)
            scores = np.array(decider.score([data.threads[i] for i in sample]))
            name = f"{settings['model']} {CLASSIFY_CONFIG['question_language']}"
            fallback = (name, sample, scores)
    finally:
        stats.uninstall()
    report = build_report(data, jev, fallback, args.threshold)
    logger.info(f"report\n{report}\n\n## Calls\n\n{stats.report()}")


if __name__ == "__main__":
    main()
