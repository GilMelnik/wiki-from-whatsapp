"""Step 2: decide per thread whether it carries shared knowledge worth extracting (contract C2).

A cheap heuristic first rejects threads that cannot hold shared knowledge (too few messages
or senders). Every other thread gets one yes/no question from the ``Classifier`` of the
``classify.classifier_stage`` stage; ``p_knowledge`` is its probability of "yes" and
``is_knowledge_bearing = p_knowledge >= classify.threshold``.

Output ``threads_classified.json`` (and its edited copy, if the user has one)::

    {"threads": [{"thread_id", "passed_heuristic", "p_knowledge", "is_knowledge_bearing"}],
     "metadata": {"inputs": {thread_id: fingerprint}, "classifier", "question", ...}}

The step is incremental (contract C14): a thread is sent to the classifier only when it is new
or its messages changed since the last run (or the classifier, question, threshold or heuristic
changed); removed threads are dropped, and a manual label in the edited file survives for every
thread whose output did not change. Only ``thread_id`` and ``messages[].{id, datetime, sender,
content}`` are read, so the old threads file and the C1 format both work.

``python -m step_2_classify.run [--rebuild] [--only THREAD_ID ...]``
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from utils.config import section
from utils.llm_client import Classifier, Question
from utils.logging_setup import setup_step_logging
from utils.paths import CLASSIFIED, STEP_2, THREADS, resolve
from utils.reconcile import diff_inputs, fingerprint, load_previous, write_reconciled
from utils.threads_io import load_threads, render_thread_for_llm

CLASSIFY_CONFIG = section("classify")
MESSAGE_FIELDS = ("id", "datetime", "sender", "content")


def thread_state(thread: dict[str, Any]) -> str:
    """Render the thread as the classifier sees it (anonymised senders, month, content).

    Only the C1 fields every threads format has are used; reactions are left out on purpose,
    so a new reaction neither changes the answer nor forces a new question.
    """
    messages = [{key: message.get(key) for key in MESSAGE_FIELDS} for message in thread["messages"]]
    rendered, _ = render_thread_for_llm({"messages": messages})
    return rendered


class ThreadClassifier:
    """The step-2 decision: heuristic pre-filter, one ``noul`` question, threshold."""

    def __init__(
        self,
        classifier: Classifier,
        question_text: str,
        *,
        threshold: float,
        min_messages: int,
        min_senders: int,
    ) -> None:
        self.classifier = classifier
        self.question = Question(CLASSIFY_CONFIG["classifier_stage"], "noul", question_text)
        self.threshold = threshold
        self.min_messages = min_messages
        self.min_senders = min_senders

    @classmethod
    def from_config(
        cls,
        logger: logging.Logger,
        *,
        language: str | None = None,
        classifier: Classifier | None = None,
    ) -> ThreadClassifier:
        """Build it from ``config.json``; ``language`` picks a question other than the configured
        one, ``classifier`` replaces the stage's classifier (both for the evaluation)."""
        questions = CLASSIFY_CONFIG["questions"]
        language = language or CLASSIFY_CONFIG["question_language"]
        if language not in questions:
            raise ValueError(
                f"classify.questions has no {language!r} question; known: {sorted(questions)}"
            )
        return cls(
            classifier or Classifier.for_stage(CLASSIFY_CONFIG["classifier_stage"], logger=logger),
            questions[language],
            threshold=CLASSIFY_CONFIG["threshold"],
            min_messages=CLASSIFY_CONFIG["min_messages"],
            min_senders=CLASSIFY_CONFIG["min_senders"],
        )

    def settings(self) -> dict[str, Any]:
        """Everything besides the thread itself that decides a record (stored in metadata)."""
        return {
            "classifier": {
                "stage": self.question.id,
                "provider": self.classifier.provider,
                "model": self.classifier.model,
            },
            "question": self.question.text,
            "threshold": self.threshold,
            "heuristic": {"min_messages": self.min_messages, "min_senders": self.min_senders},
        }

    def fingerprint(self, thread: dict[str, Any]) -> str:
        """Input fingerprint of one thread: its messages plus the decision settings."""
        messages = [[message.get(key) for key in MESSAGE_FIELDS] for message in thread["messages"]]
        return fingerprint({"messages": messages, **self.settings()})

    def passes_heuristic(self, thread: dict[str, Any]) -> bool:
        messages = thread["messages"]
        senders = {message.get("sender") for message in messages}
        return len(messages) >= self.min_messages and len(senders) >= self.min_senders

    def score(self, threads: Sequence[dict[str, Any]]) -> list[float]:
        """P(yes) per thread, in order (cached and concurrent via ``classify_many``)."""
        if not threads:
            return []
        items = [(thread_state(thread), [self.question]) for thread in threads]
        answers = self.classifier.classify_many(items, task=STEP_2)
        return [float(item_answers[0].probabilities) for item_answers in answers]

    def classify(self, threads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """One C2 record per thread; heuristic-rejected threads never reach the classifier."""
        passing = [thread for thread in threads if self.passes_heuristic(thread)]
        scores = dict(zip((thread["thread_id"] for thread in passing), self.score(passing)))
        records = []
        for thread in threads:
            p_knowledge = scores.get(thread["thread_id"])
            records.append(
                {
                    "thread_id": thread["thread_id"],
                    "passed_heuristic": p_knowledge is not None,
                    "p_knowledge": p_knowledge,
                    "is_knowledge_bearing": p_knowledge is not None
                    and p_knowledge >= self.threshold,
                }
            )
        return records


def _threads_by_id(threads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id = {thread["thread_id"]: thread for thread in threads}
    if len(by_id) != len(threads):
        raise ValueError(f"{len(threads) - len(by_id)} duplicate thread_id(s) in the threads file")
    return by_id


def run(*, rebuild: bool = False, only: Iterable[str] | None = None) -> dict[str, Any]:
    """Classify new and changed threads and reconcile ``threads_classified.json``.

    ``rebuild=True`` re-asks every thread (answers still come from the classifier cache) and
    replaces the edited copy after backing it up. ``only`` limits the classifier to those
    thread ids; other threads keep their previous record (or stay absent) and their previous
    fingerprint, so a later full run still picks them up. Returns the written metadata.
    Raises ``ClassifierError`` (nothing written; answers so far stay cached) if a request fails.
    """
    if rebuild and only is not None:
        raise ValueError("run(rebuild=True) regenerates every thread; it cannot take only=")
    logger = setup_step_logging(STEP_2)
    threads_path = resolve(THREADS)
    threads = _threads_by_id(load_threads(threads_path)["threads"])
    selected = set(threads) if only is None else set(only)
    unknown = selected - set(threads)
    if unknown:
        raise ValueError(f"only= names threads not in {threads_path}: {sorted(unknown)}")

    decider = ThreadClassifier.from_config(logger)
    previous = {} if rebuild else load_previous(CLASSIFIED)
    previous_inputs: dict[str, str] = previous.get("metadata", {}).get("inputs", {})
    carried = {record["thread_id"]: record for record in previous.get("threads", [])}
    current = {thread_id: decider.fingerprint(thread) for thread_id, thread in threads.items()}
    diff = diff_inputs(previous_inputs, current)
    stale = set(diff.new) | set(diff.changed) | (set(threads) - set(carried))
    todo = [thread_id for thread_id in threads if thread_id in selected and thread_id in stale]
    logger.info(
        f"Classify {threads_path.name}: {len(threads)} threads; new={len(diff.new)}, "
        f"changed={len(diff.changed)}, removed={len(diff.removed)}, "
        f"unchanged={len(diff.unchanged)}; asking about {len(todo)}"
    )

    fresh = {record["thread_id"]: record for record in decider.classify([threads[t] for t in todo])}
    records: list[dict[str, Any]] = []
    inputs: dict[str, str] = {}
    for thread_id in threads:
        if thread_id in fresh:
            records.append(fresh[thread_id])
            inputs[thread_id] = current[thread_id]
        elif thread_id in carried:
            records.append(carried[thread_id])
            if thread_id in previous_inputs:
                inputs[thread_id] = previous_inputs[thread_id]

    metadata = {
        "inputs": inputs,
        "source": str(threads_path),
        **decider.settings(),
        "thread_count": len(records),
        "passed_heuristic_count": sum(record["passed_heuristic"] for record in records),
        "knowledge_bearing_count": sum(record["is_knowledge_bearing"] for record in records),
        "classified_count": len(todo),
    }
    report = write_reconciled(
        CLASSIFIED,
        {"threads": records, "metadata": metadata},
        items_key="threads",
        id_key="thread_id",
        rebuild=rebuild,
    )
    logger.info(
        f"Classify: {metadata['knowledge_bearing_count']} of {len(records)} threads "
        f"knowledge-bearing; {report.changes.summary()}"
    )
    return metadata


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_2_classify.run",
        description="Decide per thread whether it carries knowledge worth extracting.",
    )
    parser.add_argument("--rebuild", action="store_true", help="re-classify every thread")
    parser.add_argument("--only", nargs="+", metavar="THREAD_ID", help="classify only these")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, only=args.only)


if __name__ == "__main__":
    main()
