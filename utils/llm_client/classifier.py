"""Non-chat classifier (contract C4): typed questions about a state, answered
with probabilities instead of generated text.

    from utils.llm_client.classifier import Classifier
    from utils.llm_client.models import Question

    classifier = Classifier.for_stage("knowledge", logger=logger)
    answers = classifier.classify(
        thread_text,
        [Question("knowledge", "noul", "The thread contains practical knowledge.")],
        task="knowledge",
    )
    p_knowledge = answers[0].probabilities  # float: probability of "yes"

    many = classifier.classify_many([(text, questions) for text in texts])
    # many[i] answers texts[i]; answers follow the order of the questions.

Provider and model per stage come from the ``classifier`` section of
``config.json``. ``fallback`` there only documents what to paste into a stage if
its provider is unavailable: the code never switches providers on its own.

Answers are cached on disk under ``<data dir>/<classifier.cache_dir>``, keyed by
provider, model, the provider's answer-changing settings, the state and the
questions. Failed requests are appended to ``<data dir>/<classifier.failure_log>``
and raised as ``ClassifierError``.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from utils.llm_client.cache import LLMCache
from utils.llm_client.failure_log import append_failure
from utils.llm_client.models import Answer, ClassifierError, Question
from utils.llm_client.providers import create_classifier_provider
from utils.llm_client.settings import CLASSIFIER_CONFIG as CONFIG, DATA_DIR


class Classifier:
    def __init__(
        self,
        provider: str,
        model: str,
        *,
        logger: logging.Logger | None = None,
        use_cache: bool = True,
        cache_dir: Path | str | None = None,
        failure_log: Path | str | None = None,
    ) -> None:
        self.provider = provider.lower()
        self.model = model
        self.logger = logger or logging.getLogger("utils.llm_client")
        self.use_cache = use_cache
        self.failure_log = Path(
            failure_log
            if failure_log is not None
            else DATA_DIR / CONFIG["failure_log"]
        )
        self._provider = create_classifier_provider(
            self.provider, self.model, self.logger
        )
        self.cache = LLMCache(
            cache_dir if cache_dir is not None else DATA_DIR / CONFIG["cache_dir"],
            {
                "provider": self.provider,
                "model": self.model,
                **self._provider.cache_identity(),
            },
        )

    @classmethod
    def for_stage(
        cls,
        stage: str,
        *,
        logger: logging.Logger | None = None,
        use_cache: bool = True,
    ) -> "Classifier":
        """Build the classifier configured for one pipeline stage."""

        stages = CONFIG["stages"]
        try:
            resolved = stages[stage]
        except KeyError:
            raise ValueError(
                f"Unknown classifier stage {stage!r}; expected one of {sorted(stages)}"
            ) from None
        return cls(
            resolved["provider"], resolved["model"], logger=logger, use_cache=use_cache
        )

    def classify(
        self, state: str, questions: Sequence[Question], task: str = ""
    ) -> list[Answer]:
        """Answer ``questions`` about ``state``; one ``Answer`` per question, in
        the same order. ``task`` only labels log lines and failure-log entries."""

        self._validate(questions)
        return self._classify(state, questions, task)

    def classify_many(
        self, items: Sequence[tuple[str, Sequence[Question]]], *, task: str = ""
    ) -> list[list[Answer]]:
        """Classify many ``(state, questions)`` items concurrently.

        Returns one answer list per item, in item order. Identical items are sent
        once. The first failing item (lowest index) raises ``ClassifierError``
        with its ``index``; requests not yet started are cancelled, while the
        items already answered stay in the disk cache, so a retry only pays for
        the rest.
        """

        for index, (_, questions) in enumerate(items):
            try:
                self._validate(questions)
            except ValueError as exc:
                raise ValueError(f"item {index}: {exc}") from exc

        keys = [self._cache_key(state, questions) for state, questions in items]
        first_index: dict[str, int] = {}
        for index, key in enumerate(keys):
            first_index.setdefault(key, index)

        started = time.monotonic()
        self.logger.info(
            f"Classifier [{self.provider}/{self.model}] task={task or '-'}: "
            f"{len(items)} items ({len(first_index)} unique), "
            f"concurrency={CONFIG['concurrency']}"
        )
        results: dict[str, list[Answer]] = {}
        pool = ThreadPoolExecutor(max_workers=CONFIG["concurrency"])
        try:
            futures = {
                key: pool.submit(self._classify, *items[index], task)
                for key, index in first_index.items()
            }
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except ClassifierError as exc:
                    exc.index = first_index[key]
                    raise
        finally:
            # On any exit (a failure, Ctrl-C) drop the requests not yet started
            # and wait only for the few in flight; a no-op after a full run.
            pool.shutdown(wait=True, cancel_futures=True)
        self.logger.info(
            f"Classifier [{self.provider}/{self.model}] task={task or '-'}: "
            f"{len(items)} items done in {time.monotonic() - started:.1f}s"
        )
        return [results[key] for key in keys]

    def _cache_key(self, state: str, questions: Sequence[Question]) -> str:
        return self.cache._cache_key(
            state=state, questions=[asdict(question) for question in questions]
        )

    def _classify(
        self, state: str, questions: Sequence[Question], task: str
    ) -> list[Answer]:
        """One cached request: read the cache, else ask the provider, log a
        failure before re-raising it."""

        label = f"Classifier [{self.provider}/{self.model}] task={task or '-'}"
        key = self._cache_key(state, questions)
        if self.use_cache:
            cached = self.cache._read_cache(key)
            if isinstance(cached, list):
                self.logger.debug(f"{label} (cache)")
                return [Answer(**entry) for entry in cached]

        started = time.monotonic()
        try:
            answers = self._provider.classify(state, questions)
        except ClassifierError as exc:
            append_failure(
                self.failure_log,
                {
                    "provider": self.provider,
                    "model": self.model,
                    "task": task,
                    "error": str(exc),
                    "state": state,
                    "questions": [asdict(question) for question in questions],
                },
                self.logger,
            )
            self.logger.warning(f"{label} FAILED: {exc} -> logged")
            raise
        self.logger.debug(
            f"{label} ({len(answers)} answers, {time.monotonic() - started:.2f}s)"
        )
        if self.use_cache:
            self.cache._write_cache(key, [asdict(answer) for answer in answers])
        return answers

    @staticmethod
    def _validate(questions: Sequence[Question]) -> None:
        """Reject question sets no provider can answer, before any paid call."""

        if not questions:
            raise ValueError("at least one question is required")
        ids = [question.id for question in questions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"question ids must be unique; got {ids}")
        max_options = {
            "choice": CONFIG["max_choice_options"],
            "score": CONFIG["max_score_levels"],
        }
        for question in questions:
            options = question.options or ()
            if question.kind == "noul":
                if options:
                    raise ValueError(
                        f"noul question {question.id!r} must not have options"
                    )
                continue
            if question.kind not in max_options:
                raise ValueError(
                    f"question {question.id!r} has unknown kind {question.kind!r}; "
                    f"expected one of {['noul', *max_options]}"
                )
            limit = max_options[question.kind]
            if not 2 <= len(options) <= limit or len(set(options)) != len(options):
                raise ValueError(
                    f"{question.kind} question {question.id!r} needs 2 to {limit} "
                    f"distinct options; got {len(options)}"
                )
