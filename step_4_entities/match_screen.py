"""Stage 2 of the model matching: the cheap screen of the candidate pairs.

The classifier stage ``entities.match.screen.stage`` (Jev, contract C4) reads the two names and
a few claims of each (``screen.claims_per_name``, spread over time) and answers one yes/no
question (``screen.question``): the probability that the two are the same real-world thing.
A pair under ``entities.match.screen_probability`` is dropped. Strong pairs (see
``match_net``) never come here.

Pairs are sent in chunks of ``screen.chunk``; when a chunk fails, its pairs are asked one by
one (the answers already given are in the disk cache), and a pair whose own request fails is
reported as unscreened: it is never silently dropped (the apply flags it for review).
``dry=True`` asks nothing: only cached answers are used and the others are counted.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_net import PairKey
from utils.config import ENTITIES_CONFIG
from utils.llm_client import Classifier, ClassifierError, Question

SCREEN_CONFIG = ENTITIES_CONFIG["match"]["screen"]
SCREEN_QUESTION = Question("same", "noul", SCREEN_CONFIG["question"])


@dataclass
class ScreenResult:
    """Probabilities of the pairs answered, the pairs that failed, and what it cost."""

    probabilities: dict[PairKey, float] = field(default_factory=dict)
    failed: list[PairKey] = field(default_factory=list)
    uncached: list[PairKey] = field(default_factory=list)  # dry run: would be asked
    requests: int = 0
    chars: int = 0


class PairScreen:
    """The screen's state text per pair and the classifier calls."""

    def __init__(
        self,
        classifier: Classifier,
        sampler: ClaimSampler,
        claims_of: Mapping[str, Sequence[str]],
        logger: logging.Logger,
    ) -> None:
        self.classifier = classifier
        self.sampler = sampler
        self.claims_of = claims_of
        self.logger = logger

    def _side(self, label: str, name: str) -> str:
        picked = self.sampler.spread(self.claims_of.get(name) or [],
                                     SCREEN_CONFIG["claims_per_name"])
        lines = [f"{label}: {name}", *(f"- {self.sampler.line(c)}" for c in picked)]
        return "\n".join(lines)

    def state(self, pair: PairKey) -> str:
        """What the classifier reads about one pair (the names in sorted order)."""

        return f"{self._side('Name A', pair[0])}\n\n{self._side('Name B', pair[1])}"

    def screen(self, pairs: Sequence[PairKey], *, dry: bool = False) -> ScreenResult:
        result = ScreenResult()
        states = {pair: self.state(pair) for pair in pairs}
        if dry:
            for pair, state in states.items():
                cached = self.classifier.cached(state, [SCREEN_QUESTION])
                if cached is None:
                    result.uncached.append(pair)
                    result.chars += len(state) + len(SCREEN_QUESTION.text)
                else:
                    result.probabilities[pair] = float(cached[0].probabilities)
            return result
        size = SCREEN_CONFIG["chunk"]
        for start in range(0, len(pairs), size):
            chunk = list(pairs[start:start + size])
            items = [(states[pair], [SCREEN_QUESTION]) for pair in chunk]
            result.chars += sum(len(state) + len(SCREEN_QUESTION.text) for state, _ in items)
            result.requests += len(items)
            try:
                answers = self.classifier.classify_many(items, task="entity-screen")
            except ClassifierError as exc:
                self.logger.warning(f"Entity screen: a chunk of {len(chunk)} pairs failed "
                                    f"({exc}); asking its pairs one by one")
                self._one_by_one(chunk, states, result)
                continue
            for pair, answer in zip(chunk, answers):
                result.probabilities[pair] = float(answer[0].probabilities)
        return result

    def _one_by_one(self, chunk: Sequence[PairKey], states: Mapping[PairKey, str],
                    result: ScreenResult) -> None:
        for pair in chunk:
            try:
                answer = self.classifier.classify(states[pair], [SCREEN_QUESTION],
                                                  task="entity-screen")
            except ClassifierError:
                result.failed.append(pair)
                continue
            result.probabilities[pair] = float(answer[0].probabilities)
        if result.failed:
            self.logger.warning(f"Entity screen: {len(result.failed)} pairs could not be "
                                "screened; they are flagged for review")
