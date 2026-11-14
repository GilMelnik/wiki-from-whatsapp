"""Score a thread split against the frozen gold windows; CLI for the gold workflow.

``evaluate(pred_threads, gold)`` compares any thread split with the gold file written by
``step_1_threads_split.gold.freeze_gold``, one window at a time, over the gold messages
only. Messages are matched on their WhatsApp id (``messages[].id``), so the old threads
format and the C1 format score identically.

``python -m step_1_threads_split.evaluate``:

- no mode flag: score ``--pred`` (default: the original threads file) against ``--gold``;
- ``--suggest-windows [N] [--hours H] [--seed S]``: propose windows to tag;
- ``--freeze-gold [--add | --force] [--done-only]``: build the gold file from
  ``threads_edited.json`` and ``config.json`` ``threads.gold_windows``; ``--done-only``
  keeps only the windows marked done in the conversations tool (``gold_progress.json``);
- ``--add-context --tagged PATH [--shown PATH ...]``: add the context part (the owner's tags
  outside the windows, ``step_1_threads_split.gold_context``) to the gold file;
- ``--context`` (with the scoring mode): also report the "window + context" score.

Each mode logs one markdown report (console and step log file).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from step_1_threads_split.gold import (
    Window,
    WindowSet,
    WindowSuggester,
    freeze_gold,
    load_gold,
    message_moment,
)
from step_1_threads_split.gold_context import add_context_to_file, all_labels, scored_ids
from step_1_threads_split.gold_progress import GoldProgress
from utils.config import section
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import (
    BACKUPS_DIR,
    EDITED_THREADS_PATH,
    GOLD_PROGRESS_PATH,
    GOLD_THREADS_PATH,
    ORIGINAL_THREADS_PATH,
    STEP_1,
)
from utils.threads_io import load_threads

THREADS_CONFIG = section("threads")
logger = logging.getLogger(STEP_1)


# -------------------------------------------------------------------- metrics
def _ratio(numerator: float, denominator: float) -> float:
    """``numerator / denominator``, or 1.0 when there was nothing to get wrong."""
    return numerator / denominator if denominator else 1.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


@dataclass
class ClusterCounts:
    """Additive agreement counts between a predicted and a gold clustering."""

    messages: int = 0
    pred_clusters: int = 0
    gold_clusters: int = 0
    same_both_pairs: int = 0
    same_pred_pairs: int = 0
    same_gold_pairs: int = 0
    bcubed_precision_sum: float = 0.0
    bcubed_recall_sum: float = 0.0
    missing_in_pred: int = 0
    unlabelled_in_pred: int = 0

    @classmethod
    def from_labels(cls, pred: Sequence[Hashable], gold: Sequence[Hashable]) -> ClusterCounts:
        """Count agreement; ``pred[i]`` and ``gold[i]`` are the clusters of message ``i``."""
        joint = Counter(zip(pred, gold, strict=True))
        pred_sizes = Counter(pred)
        gold_sizes = Counter(gold)
        return cls(
            messages=len(pred),
            pred_clusters=len(pred_sizes),
            gold_clusters=len(gold_sizes),
            same_both_pairs=sum(n * (n - 1) // 2 for n in joint.values()),
            same_pred_pairs=sum(n * (n - 1) // 2 for n in pred_sizes.values()),
            same_gold_pairs=sum(n * (n - 1) // 2 for n in gold_sizes.values()),
            bcubed_precision_sum=sum(n * n / pred_sizes[p] for (p, _), n in joint.items()),
            bcubed_recall_sum=sum(n * n / gold_sizes[g] for (_, g), n in joint.items()),
        )

    def __add__(self, other: ClusterCounts) -> ClusterCounts:
        return ClusterCounts(
            *(getattr(self, field.name) + getattr(other, field.name) for field in fields(self))
        )

    def metrics(self) -> dict[str, float]:
        pairwise_precision = _ratio(self.same_both_pairs, self.same_pred_pairs)
        pairwise_recall = _ratio(self.same_both_pairs, self.same_gold_pairs)
        bcubed_precision = _ratio(self.bcubed_precision_sum, self.messages)
        bcubed_recall = _ratio(self.bcubed_recall_sum, self.messages)
        return {
            "messages": self.messages,
            "pred_clusters": self.pred_clusters,
            "gold_clusters": self.gold_clusters,
            "pairwise_precision": pairwise_precision,
            "pairwise_recall": pairwise_recall,
            "pairwise_f1": _f1(pairwise_precision, pairwise_recall),
            "bcubed_precision": bcubed_precision,
            "bcubed_recall": bcubed_recall,
            "bcubed_f1": _f1(bcubed_precision, bcubed_recall),
            "missing_in_pred": self.missing_in_pred,
            "unlabelled_in_pred": self.unlabelled_in_pred,
        }


def evaluate(
    pred_threads: list[dict[str, Any]], gold: dict[str, Any], *, with_context: bool = False
) -> dict[str, Any]:
    """Score a thread split against the frozen gold clustering.

    ``pred_threads`` is the ``threads`` list of a threads file (old or C1 shape; only
    ``messages[].id`` and ``messages[].datetime`` are read). ``gold`` comes from
    ``load_gold``. Only gold messages are scored, each window on its own: pairwise
    precision/recall/F1 over same-thread message pairs and B-cubed precision/recall/F1.
    A gold message the prediction does not contain counts as a predicted thread of its
    own (``missing_in_pred``); a predicted message inside a window that has no gold label
    is ignored (``unlabelled_in_pred``). Returns ``{"windows": [{"start", "end",
    **metrics}], "micro": metrics}`` where ``micro`` pools the counts of all windows.
    ``with_context`` also scores each window's context messages (the wider score of
    ``step_1_threads_split.gold_context``).
    """
    gold_windows = sorted(gold["windows"], key=lambda window: window["start"])
    window_set = WindowSet(Window.from_dict(window) for window in gold_windows)
    labels: dict[str, str] = all_labels(gold) if with_context else gold["messages"]
    pred_cluster: dict[str, int] = {}
    unlabelled: Counter[int] = Counter()
    for thread_index, thread in enumerate(pred_threads):
        for message in thread["messages"]:
            wa_id = message["id"]
            if wa_id in labels:
                if wa_id in pred_cluster:
                    raise ValueError(f"message {wa_id} is in more than one predicted thread")
                pred_cluster[wa_id] = thread_index
                continue
            window_index = window_set.index_of(message_moment(message))
            if window_index is not None:
                unlabelled[window_index] += 1

    per_window: list[ClusterCounts] = []
    for window_index, window in enumerate(gold_windows):
        wa_ids = scored_ids(gold, window, with_context)
        counts = ClusterCounts.from_labels(
            [pred_cluster.get(wa_id, wa_id) for wa_id in wa_ids],
            [labels[wa_id] for wa_id in wa_ids],
        )
        counts.missing_in_pred = sum(1 for wa_id in wa_ids if wa_id not in pred_cluster)
        counts.unlabelled_in_pred = unlabelled[window_index]
        per_window.append(counts)
    return {
        "windows": [
            {"start": window["start"], "end": window["end"], **counts.metrics()}
            for window, counts in zip(gold_windows, per_window)
        ],
        "micro": sum(per_window, ClusterCounts()).metrics(),
    }


# -------------------------------------------------------------------- reports
def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    lines.extend(" | ".join(str(cell) for cell in row) for row in rows)
    return "\n".join(f"| {line} |" for line in lines)


def _span(window: dict[str, Any]) -> str:
    return f"{window['start']} → {window['end']}"


def format_evaluation(result: dict[str, Any]) -> str:
    """Markdown table: one row per gold window plus the micro-average."""
    columns = {
        "messages": "messages",
        "pred_clusters": "pred threads",
        "gold_clusters": "gold threads",
        "pairwise_precision": "pair P",
        "pairwise_recall": "pair R",
        "pairwise_f1": "pair F1",
        "bcubed_precision": "B3 P",
        "bcubed_recall": "B3 R",
        "bcubed_f1": "B3 F1",
        "missing_in_pred": "missing in pred",
        "unlabelled_in_pred": "unlabelled in pred",
    }

    def row(name: str, metrics: dict[str, Any]) -> list[str]:
        cells = (metrics[key] for key in columns)
        return [name, *(f"{cell:.3f}" if isinstance(cell, float) else str(cell) for cell in cells)]

    rows = [row(_span(window), window) for window in result["windows"]]
    rows.append(row("**micro**", result["micro"]))
    return markdown_table(["window", *columns.values()], rows)


def format_suggestions(suggestions: list[dict[str, Any]]) -> str:
    """Markdown: per window its threads, then a ``gold_windows`` block for config.json."""
    total = sum(suggestion["message_count"] for suggestion in suggestions)
    parts = [f"{len(suggestions)} proposed windows, {total} messages in total."]
    for number, suggestion in enumerate(suggestions, start=1):
        parts.append(
            f"### Window {number}: {_span(suggestion)} (messages: "
            f"{suggestion['message_count']}, predicted threads: {suggestion['thread_count']})"
        )
        parts.append(
            markdown_table(
                ["thread id", "thread starts", "messages in window", "messages in thread"],
                (
                    [
                        thread["thread_id"],
                        thread["start_time"].replace("T", " ")[:16],
                        thread["messages_in_window"],
                        thread["num_messages"],
                    ]
                    for thread in suggestion["threads"]
                ),
            )
        )
    windows = [{"start": s["start"], "end": s["end"]} for s in suggestions]
    snippet = json.dumps({"gold_windows": windows}, indent=2)
    parts.append(f"For config.json `threads`:\n```json\n{snippet}\n```")
    return "\n\n".join(parts)


def format_gold(gold: dict[str, Any]) -> str:
    """Markdown table: messages and gold threads per frozen window."""
    labels = gold["messages"]
    rows = [
        [
            _span(window),
            window["frozen_at"],
            len(window["message_ids"]),
            len({labels[wa_id] for wa_id in window["message_ids"]}),
        ]
        for window in gold["windows"]
    ]
    metadata = gold["metadata"]
    rows.append(["**total**", "", metadata["message_count"], metadata["cluster_count"]])
    return markdown_table(["window", "frozen at", "messages", "gold threads"], rows)


# ------------------------------------------------------------------------ CLI
def _configured_windows() -> list[Window]:
    return [Window.from_dict(raw) for raw in THREADS_CONFIG["gold_windows"]]


def _done_windows() -> list[Window]:
    """The configured windows the user marked done in the conversations tool."""
    configured = _configured_windows()
    done = GoldProgress(GOLD_PROGRESS_PATH).done_windows()
    chosen = [window for window in configured if window in done]
    if not chosen:
        raise ValueError(
            f"none of the {len(configured)} configured gold windows is marked done in "
            f"{GOLD_PROGRESS_PATH}; mark windows done in the conversations tool (`python -m review_tool --tool threads`) first"
        )
    for window in configured:
        if window not in done:
            logger.info(f"--done-only: skipping gold window {window} (not marked done)")
    return chosen


def _suggest_report(args: argparse.Namespace) -> str:
    defaults = THREADS_CONFIG["gold_suggest"]
    taken = _configured_windows()
    if args.gold.is_file():
        taken.extend(Window.from_dict(window) for window in load_gold(args.gold)["windows"])
    suggester = WindowSuggester(
        load_threads(args.pred)["threads"],
        hours=args.hours,
        min_messages=defaults["min_messages"],
        max_messages=defaults["max_messages"],
        min_threads=defaults["min_threads"],
        exclude=taken,
    )
    suggestions = suggester.suggest(args.suggest_windows, args.seed)
    if len(suggestions) < args.suggest_windows:
        logger.warning(
            f"only {len(suggestions)} of {args.suggest_windows} windows found in {args.pred}; "
            "relax threads.gold_suggest min_messages/max_messages or change --hours"
        )
    return format_suggestions(suggestions)


def _freeze_report(args: argparse.Namespace) -> str:
    if not EDITED_THREADS_PATH.is_file():
        raise FileNotFoundError(
            f"{EDITED_THREADS_PATH} not found; the gold file is frozen from the threads "
            "tagged in the conversations tool (`python -m review_tool --tool threads`)"
        )
    kept = load_gold(args.gold)["windows"] if args.add and args.gold.is_file() else []
    frozen_before = {window["start"] for window in kept}
    gold = freeze_gold(
        load_threads(EDITED_THREADS_PATH)["threads"],
        _done_windows() if args.done_only else _configured_windows(),
        args.gold,
        BACKUPS_DIR,
        str(EDITED_THREADS_PATH),
        add=args.add,
        force=args.force,
    )
    if ORIGINAL_THREADS_PATH.is_file():
        untouched = evaluate(load_threads(ORIGINAL_THREADS_PATH)["threads"], gold)
        for scores in untouched["windows"]:
            same = scores["pairwise_precision"] == scores["pairwise_recall"] == 1.0
            if same and scores["start"] not in frozen_before:
                logger.warning(
                    f"gold window {_span(scores)} is identical to the un-edited "
                    f"{ORIGINAL_THREADS_PATH.name}; make sure it was really tagged"
                )
    return format_gold(gold)


def _evaluation_report(args: argparse.Namespace) -> str:
    threads = load_threads(args.pred)["threads"]
    gold = load_gold(args.gold)
    parts = []
    for with_context in (False, True) if args.context else (False,):
        result = evaluate(threads, gold, with_context=with_context)
        if result["micro"]["missing_in_pred"]:
            logger.warning(
                f"{result['micro']['missing_in_pred']} gold messages are not in {args.pred}; "
                "each was scored as a predicted thread of its own"
            )
        title = "window + context" if with_context else "window"
        parts.append(f"{title}:\n{format_evaluation(result)}")
    return "\n\n".join(parts)


def _context_report(args: argparse.Namespace) -> str:
    """Add the context part to the gold file (backed up first); see ``gold_context``."""
    if args.tagged is None:
        raise ValueError("--add-context needs --tagged: the owner's tagged threads file")
    updated = add_context_to_file(args.gold, args.tagged, args.shown)
    rows = [
        [_span(window), len(window["message_ids"]), len(window["context_message_ids"])]
        for window in updated["windows"]
    ]
    return markdown_table(["window", "messages", "context messages"], rows)


def main(argv: Sequence[str] | None = None) -> None:
    defaults = THREADS_CONFIG["gold_suggest"]
    parser = argparse.ArgumentParser(
        prog="python -m step_1_threads_split.evaluate",
        description="Score a thread split against the gold windows, freeze the gold file, "
        "or propose windows to tag.",
    )
    parser.add_argument(
        "--pred",
        type=Path,
        default=ORIGINAL_THREADS_PATH,
        help="threads file to score, or to propose windows from (default: %(default)s)",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=GOLD_THREADS_PATH,
        help="frozen gold file to read or write (default: %(default)s)",
    )
    parser.add_argument(
        "--suggest-windows",
        type=int,
        nargs="?",
        const=defaults["windows"],
        metavar="N",
        help="propose N windows to tag (default N: %(const)s)",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=defaults["hours"],
        help="length of a proposed window in hours (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=defaults["seed"],
        help="seed of the window draw (default: %(default)s)",
    )
    parser.add_argument(
        "--freeze-gold",
        action="store_true",
        help="build the gold file from threads_edited.json and threads.gold_windows",
    )
    parser.add_argument(
        "--add",
        action="store_true",
        help="with --freeze-gold: merge new windows into the existing gold file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --freeze-gold: rebuild an existing gold file from scratch",
    )
    parser.add_argument(
        "--done-only",
        action="store_true",
        help="with --freeze-gold: freeze only the windows marked done in the conversations tool",
    )
    parser.add_argument(
        "--context",
        action="store_true",
        help="also score each window together with its context messages",
    )
    parser.add_argument(
        "--add-context",
        action="store_true",
        help="add the context part to the gold file from --tagged (and --shown)",
    )
    parser.add_argument(
        "--tagged", type=Path, help="with --add-context: the owner's tagged threads file"
    )
    parser.add_argument(
        "--shown",
        type=Path,
        nargs="*",
        default=[],
        help="with --add-context: further threads files whose threads the tagger showed",
    )
    args = parser.parse_args(argv)
    if (args.add or args.force or args.done_only) and not args.freeze_gold:
        parser.error("--add, --force and --done-only only apply to --freeze-gold")
    if args.add and args.force:
        parser.error("--add and --force are mutually exclusive")
    modes = [args.freeze_gold, args.suggest_windows is not None, args.add_context]
    if sum(modes) > 1:
        parser.error("--freeze-gold, --suggest-windows and --add-context are separate runs")
    if (args.tagged or args.shown) and not args.add_context:
        parser.error("--tagged and --shown only apply to --add-context")

    setup_step_logging(STEP_1)
    random.seed(args.seed)
    logger.info(
        f"evaluate | git commit {git_commit()} | args {vars(args)} | "
        f"config.threads {json.dumps(THREADS_CONFIG)}"
    )
    if args.freeze_gold:
        report = _freeze_report(args)
    elif args.add_context:
        report = _context_report(args)
    elif args.suggest_windows is not None:
        report = _suggest_report(args)
    else:
        report = _evaluation_report(args)
    logger.info(f"report\n{report}")


if __name__ == "__main__":
    main()
