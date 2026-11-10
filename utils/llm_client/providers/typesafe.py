"""TypeSafe "System One" classifier provider (the Jev models).

One request sends a ``state`` plus named, typed questions and returns one typed
answer per question (``POST https://api.typesafe.ai/v1/systemone``, via the
official ``typesafe-sdk``; the key is read from ``TYPESAFE_API_KEY``). Mapping of
the C4 question kinds:

- ``noul``   -> ``Noul(instructions=text)``; answer ``noul`` = P(yes).
- ``choice`` -> ``Choice(instructions=text, criteria={option: None, ...})``;
  answer ``probabilities`` per option (they sum to ~1).
- ``score``  -> ``Score(instructions=text, criteria=[level, ...])``; the API
  returns the expected position on the levels (0 .. last level), normalised
  here to 0..1.

Service limits: 64k tokens per request (state + all questions) and 32k per
question (state + that question); up to 255 choice options and 10 score levels;
no batch endpoint, so many items are sent as concurrent requests. Rate limits
are per organisation; the SDK retries 429/5xx itself and honours ``retry-after``.
"""

from __future__ import annotations

from typing import Sequence

from typesafe_sdk import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    TypeSafeClient,
    TypeSafeError,
)

from utils.llm_client.models import Answer, ClassifierError, Question
from utils.llm_client.providers.base import ClassifierProvider


class TypeSafeClassifierProvider(ClassifierProvider):
    name = "typesafe"

    def _create_client(self) -> TypeSafeClient:
        return TypeSafeClient(model=self.model)

    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        try:
            response = self._get_client().system_one(
                state, {question.id: self._sdk_question(question) for question in questions}
            )
        except TypeSafeError as exc:
            raise ClassifierError(
                f"TypeSafe {self.model} request failed: {exc!r}"
            ) from exc
        self.logger.debug(
            f"TypeSafe {response.model}: input_tokens={response.usage.input_tokens}"
        )
        return [
            self._to_answer(question, response.answers[question.id])
            for question in questions
        ]

    @staticmethod
    def _sdk_question(question: Question) -> Noul | Choice | Score:
        options = question.options or ()
        if question.kind == "noul":
            return Noul(instructions=question.text)
        if question.kind == "choice":
            return Choice(
                instructions=question.text,
                criteria={option: None for option in options},
            )
        return Score(instructions=question.text, criteria=list(options))

    def _to_answer(
        self, question: Question, answer: NoulAnswer | ChoiceAnswer | ScoreAnswer
    ) -> Answer:
        if isinstance(answer, NoulAnswer):
            return Answer(question.id, answer.noul)
        if isinstance(answer, ChoiceAnswer):
            return Answer(question.id, dict(answer.probabilities))
        return Answer(question.id, self._normalised_score(question, answer.score))
