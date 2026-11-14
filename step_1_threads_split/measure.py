"""Measure the splitter against the gold windows and the old splitter.

``python -m step_1_threads_split.measure [--baseline PATH] [--gold PATH]`` splits the whole
chat export for every parameter combination of ``config.json`` ``threads.measure.grid`` and
writes one markdown report (logged, and saved to ``MEASURE_REPORT_PATH``) with:

- the gold table (window, and window + context) of the old splitter and of the configured
  parameters;
- held-out windows: for each window in turn, the parameters chosen on the other seven
  (best pooled B-cubed F1; ties broken by their window + context score, then grid order)
  and that window's score with them;
- the edge analysis (``gold_context.edge_cut_labels``): the headline without the threads cut
  by an edge, and every remaining disagreement as counts and message ids;
- thread sizes against the old splitter;
- the incremental check: the first part of the chat split, the rest added with
  ``assign_new_messages``, compared with the full split (``threads.measure.incremental_cuts``).

Only message ids and times are reported, never text or names. Embeddings come from the cache
of ``utils.embedder`` (paid only for texts never embedded before).
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from step_1_threads_split.evaluate import ClusterCounts, _f1, evaluate
from step_1_threads_split.gold_context import edge_cut_labels, without_labels
from step_1_threads_split.splitter import EmbedFn, SplitParams, ThreadSplitter, embedding_text
from utils.config import section
from utils.embedder import embed_texts
from utils.export_messages import load_export_messages
from utils.gold_threads import all_labels, load_gold, scored_ids
from utils.logging_setup import git_commit, setup_step_logging
from utils.markdown_table import markdown_table
from utils.paths import CHAT_ANDROID_PATH, GOLD_THREADS_PATH, MEASURE_REPORT_PATH, STEP_1, step_path
from utils.threads_io import load_threads

THREADS_CONFIG = section("threads")
MEASURE_CONFIG = THREADS_CONFIG["measure"]
logger = logging.getLogger(STEP_1)


def memo_embed(messages: Sequence[dict[str, Any]]) -> EmbedFn:
    """An embed function that serves the chat's texts from memory (one cache read)."""
    texts = list(dict.fromkeys(embedding_text(m) for m in messages))
    vectors = dict(zip(texts, embed_texts(texts, THREADS_CONFIG["embedding_kind"])))

    def embed(batch: Sequence[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(batch) if t not in vectors]
        if missing:
            vectors.update(zip(missing, embed_texts(missing, THREADS_CONFIG["embedding_kind"])))
        return np.stack([vectors[t] for t in batch]) if batch else np.zeros((0, 0))

    return embed


def pooled_f1(windows: Sequence[dict[str, Any]]) -> float:
    """Micro B-cubed F1 of a subset of windows, pooled from their per-window metrics."""
    total = sum(w["messages"] for w in windows)
    precision = sum(w["bcubed_precision"] * w["messages"] for w in windows) / total
    recall = sum(w["bcubed_recall"] * w["messages"] for w in windows) / total
    return _f1(precision, recall)


def disagreements(
    threads: Sequence[dict[str, Any]], gold: dict[str, Any], cut: Sequence[set[str]], with_context: bool
) -> list[dict[str, Any]]:
    """Every cell where a prediction and the gold disagree, per window.

    A ``merge`` is a gold thread's messages sitting in a predicted thread whose largest part
    belongs to another gold thread; a ``split`` is a part of a gold thread outside the
    predicted thread that holds most of it. ``edge`` tells whether a gold thread involved is
    cut by an edge of what the owner saw.
    """
    owner = {m["id"]: t["thread_id"] for t in threads for m in t["messages"]}
    labels = all_labels(gold)
    found: list[dict[str, Any]] = []
    for index, window in enumerate(gold["windows"]):
        cells: dict[tuple[str, str], list[str]] = {}
        for wa_id in scored_ids(gold, window, with_context):
            cells.setdefault((owner.get(wa_id, wa_id), labels[wa_id]), []).append(wa_id)
        for kind, side in (("merge", 0), ("split", 1)):
            groups: dict[str, list[tuple[str, str]]] = {}
            for cell in cells:
                groups.setdefault(cell[side], []).append(cell)
            for members in groups.values():
                main = max(members, key=lambda cell: (len(cells[cell]), cell))
                for cell in members:
                    if cell == main:
                        continue
                    involved = {cell[1], main[1]}
                    found.append(
                        {
                            "window": window["start"],
                            "kind": kind,
                            "edge": bool(involved & cut[index]),
                            "message_ids": cells[cell],
                        }
                    )
    return found


def size_summary(threads: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sizes = sorted(len(t["messages"]) for t in threads)
    return {
        "threads": len(sizes),
        "messages": sum(sizes),
        "singletons": sum(1 for s in sizes if s == 1),
        "median": statistics.median(sizes),
        "p90": sizes[int(0.9 * (len(sizes) - 1))],
        "max": sizes[-1],
        "share_in_10_plus": round(sum(s for s in sizes if s >= 10) / sum(sizes), 3),
    }


def agreement(reference: Sequence[dict[str, Any]], other: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How far ``other`` is from ``reference`` over all messages (B-cubed, ``reference`` = gold)."""
    ref = {m["id"]: t["thread_id"] for t in reference for m in t["messages"]}
    oth = {m["id"]: t["thread_id"] for t in other for m in t["messages"]}
    ids = sorted(ref)
    metrics = ClusterCounts.from_labels([oth[i] for i in ids], [ref[i] for i in ids]).metrics()
    members_ref: dict[str, set[str]] = {}
    members_oth: dict[str, set[str]] = {}
    for i in ids:
        members_ref.setdefault(ref[i], set()).add(i)
        members_oth.setdefault(oth[i], set()).add(i)
    moved = sum(1 for i in ids if members_ref[ref[i]] != members_oth[oth[i]])
    return {
        "bcubed_f1": metrics["bcubed_f1"],
        "messages_in_a_different_thread": moved,
        "messages": len(ids),
    }


class Measurement:
    """One chat, one gold file, one old split: every number of the report."""

    def __init__(
        self, messages: list[dict[str, Any]], gold: dict[str, Any], baseline: list[dict[str, Any]]
    ) -> None:
        self.messages = messages
        self.gold = gold
        self.baseline = baseline
        self.embed = memo_embed(messages)
        self.cut = edge_cut_labels(gold, [m["id"] for m in messages])

    def split(self, params: SplitParams) -> list[dict[str, Any]]:
        return ThreadSplitter(params, self.embed).split(self.messages)

    def scores(self, threads: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return {
            "window": evaluate(list(threads), self.gold),
            "context": evaluate(list(threads), self.gold, with_context=True),
        }

    def grid(self) -> list[tuple[SplitParams, dict[str, Any]]]:
        axes = MEASURE_CONFIG["grid"]
        results = []
        for values in itertools.product(*axes.values()):
            params = SplitParams(**dict(zip(axes, values)))
            results.append((params, self.scores(self.split(params))))
        return results

    @staticmethod
    def choose(grid: Sequence[tuple[SplitParams, dict[str, Any]]], train: Sequence[int]) -> int:
        """The grid row with the best pooled F1 on the ``train`` windows (see module doc)."""

        def key(row: int) -> tuple[float, float, int]:
            scores = grid[row][1]
            window = pooled_f1([scores["window"]["windows"][i] for i in train])
            context = pooled_f1([scores["context"]["windows"][i] for i in train])
            return round(window, 9), round(context, 9), -row

        return max(range(len(grid)), key=key)

    def held_out(self, grid: Sequence[tuple[SplitParams, dict[str, Any]]]) -> list[dict[str, Any]]:
        count = len(self.gold["windows"])
        rows = []
        for held in range(count):
            row = self.choose(grid, [i for i in range(count) if i != held])
            params, scores = grid[row]
            rows.append(
                {
                    "window": self.gold["windows"][held]["start"],
                    "params": params.to_dict(),
                    "held_out": scores["window"]["windows"][held],
                    "held_out_context": scores["context"]["windows"][held],
                }
            )
        return rows

    def edges(self, threads: Sequence[dict[str, Any]]) -> dict[str, Any]:
        trimmed = without_labels(self.gold, self.cut)
        found = disagreements(threads, self.gold, self.cut, with_context=False)
        found_context = disagreements(threads, self.gold, self.cut, with_context=True)
        return {
            "without_edge_threads": evaluate(list(threads), trimmed)["micro"],
            "without_edge_threads_context": evaluate(list(threads), trimmed, with_context=True)[
                "micro"
            ],
            "window": found,
            "context": found_context,
        }

    def incremental(self, params: SplitParams, full: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for fraction in MEASURE_CONFIG["incremental_cuts"]:
            cut = int(len(self.messages) * fraction)
            splitter = ThreadSplitter(params, self.embed)
            first = splitter.split(self.messages[:cut])
            added = splitter.assign_new_messages(first, self.messages[cut:])
            rows.append(
                {
                    "cut": fraction,
                    "first_part_messages": cut,
                    "headline_bcubed_f1": evaluate(added, self.gold)["micro"]["bcubed_f1"],
                    **agreement(full, added),
                    "threads": len(added),
                }
            )
        return rows


def _metric_row(name: str, metrics: dict[str, Any]) -> list[str]:
    keys = ("bcubed_precision", "bcubed_recall", "bcubed_f1", "pairwise_f1")
    return [name, *(f"{metrics[k]:.3f}" for k in keys), str(metrics["pred_clusters"]),
            str(metrics["gold_clusters"]), str(metrics["missing_in_pred"])]  # fmt: skip


def _disagreement_table(found: Sequence[dict[str, Any]]) -> str:
    counts: Counter[tuple[str, bool]] = Counter()
    sizes: Counter[tuple[str, bool]] = Counter()
    for item in found:
        counts[(item["kind"], item["edge"])] += 1
        sizes[(item["kind"], item["edge"])] += len(item["message_ids"])
    rows = [
        [kind, "edge (uncertain)" if edge else "inside (error)", counts[(kind, edge)],
         sizes[(kind, edge)]]  # fmt: skip
        for kind in ("merge", "split")
        for edge in (False, True)
    ]
    return markdown_table(["kind", "gold thread", "cases", "messages"], rows)


def _gold_threads(gold: dict[str, Any]) -> list[int]:
    labels = all_labels(gold)
    return [len({labels[i] for i in scored_ids(gold, w, True)}) for w in gold["windows"]]


def report(measurement: Measurement, params: SplitParams) -> tuple[str, dict[str, Any]]:
    """The markdown report and its numbers as JSON-ready data."""
    started = time.monotonic()
    grid = measurement.grid()
    new = measurement.split(params)
    old_scores = measurement.scores(measurement.baseline)
    new_scores = measurement.scores(new)
    count = len(measurement.gold["windows"])
    best_row = Measurement.choose(grid, range(count))
    held = measurement.held_out(grid)
    data = {
        "params": params.to_dict(),
        "best_on_all_windows": grid[best_row][0].to_dict(),
        "old": old_scores,
        "new": new_scores,
        "held_out": held,
        "held_out_micro": pooled_f1([row["held_out"] for row in held]),
        "edges": {"old": measurement.edges(measurement.baseline), "new": measurement.edges(new)},
        "sizes": {"old": size_summary(measurement.baseline), "new": size_summary(new)},
        "incremental": measurement.incremental(params, new),
        "grid_rows": len(grid),
        "near_best": sum(
            1
            for _, scores in grid
            if scores["window"]["micro"]["bcubed_f1"]
            >= grid[best_row][1]["window"]["micro"]["bcubed_f1"] - 0.005
        ),
        "seconds": round(time.monotonic() - started, 1),
    }
    header = ["split", "B3 P", "B3 R", "B3 F1", "pair F1", "pred threads", "gold threads", "missing"]
    parts = [
        f"configured parameters: {json.dumps(data['params'])}; best on all windows: "
        f"{json.dumps(data['best_on_all_windows'])} ({data['near_best']} of {len(grid)} grid "
        "rows within 0.005 of the best)",
        markdown_table(header, [
            _metric_row("old, window", old_scores["window"]["micro"]),
            _metric_row("new, window", new_scores["window"]["micro"]),
            _metric_row("old, window + context", old_scores["context"]["micro"]),
            _metric_row("new, window + context", new_scores["context"]["micro"]),
            _metric_row("old, window, no edge threads", data["edges"]["old"]["without_edge_threads"]),
            _metric_row("new, window, no edge threads", data["edges"]["new"]["without_edge_threads"]),
            _metric_row("old, + context, no edge threads",
                        data["edges"]["old"]["without_edge_threads_context"]),
            _metric_row("new, + context, no edge threads",
                        data["edges"]["new"]["without_edge_threads_context"]),
        ]),  # fmt: skip
        markdown_table(
            ["window", "old F1", "new F1", "old F1 +ctx", "new F1 +ctx", "held-out F1",
             "held-out params"],  # fmt: skip
            [
                [row["window"],
                 f"{old_scores['window']['windows'][i]['bcubed_f1']:.3f}",
                 f"{new_scores['window']['windows'][i]['bcubed_f1']:.3f}",
                 f"{old_scores['context']['windows'][i]['bcubed_f1']:.3f}",
                 f"{new_scores['context']['windows'][i]['bcubed_f1']:.3f}",
                 f"{row['held_out']['bcubed_f1']:.3f}",
                 json.dumps(row["params"])]  # fmt: skip
                for i, row in enumerate(held)
            ],
        ),
        f"held-out micro B3 F1 (eight windows, each scored with parameters chosen on the "
        f"other seven): {data['held_out_micro']:.3f}",
        "gold threads cut by an edge of what was shown, per window: "
        + ", ".join(
            f"{len(cut)} of {threads}" for cut, threads in zip(measurement.cut, _gold_threads(measurement.gold))
        ),
    ]
    for side in ("old", "new"):
        for scope in ("window", "context"):
            parts.append(f"disagreements, {side} splitter, {scope}:")
            parts.append(_disagreement_table(data["edges"][side][scope]))
    parts.append("disagreements of the new splitter inside the windows (message ids):")
    parts.append(markdown_table(["window", "kind", "edge", "message ids"], [
        [item["window"], item["kind"], item["edge"], " ".join(item["message_ids"])]
        for item in data["edges"]["new"]["window"]
    ]))  # fmt: skip
    parts.append(markdown_table(list(data["sizes"]["new"]), [
        list(data["sizes"]["old"].values()), list(data["sizes"]["new"].values())
    ]))  # fmt: skip
    parts.append(markdown_table(list(data["incremental"][0]), [
        [f"{v:.3f}" if isinstance(v, float) else v for v in row.values()]
        for row in data["incremental"]
    ]))  # fmt: skip
    return "\n\n".join(parts), data


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_1_threads_split.measure",
        description="Score the splitter on the gold windows against the old splitter.",
    )
    parser.add_argument("--export", type=Path, default=CHAT_ANDROID_PATH)
    parser.add_argument("--gold", type=Path, default=GOLD_THREADS_PATH)
    parser.add_argument(
        "--baseline", type=Path, default=step_path(STEP_1, MEASURE_CONFIG["baseline_file"])
    )
    args = parser.parse_args(argv)
    setup_step_logging(STEP_1)
    random.seed(0)
    np.random.seed(0)
    params = SplitParams.from_config()
    logger.info(
        f"measure | git commit {git_commit()} | args {vars(args)} | "
        f"config.threads.split {json.dumps(params.to_dict())} | "
        f"grid {json.dumps(MEASURE_CONFIG['grid'])}"
    )
    measurement = Measurement(
        load_export_messages(args.export), load_gold(args.gold), load_threads(args.baseline)["threads"]
    )
    text, data = report(measurement, params)
    MEASURE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEASURE_REPORT_PATH.write_text(text + "\n", encoding="utf-8")
    MEASURE_REPORT_PATH.with_suffix(".json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"report ({data['seconds']} s) -> {MEASURE_REPORT_PATH}\n{text}")


if __name__ == "__main__":
    main()
