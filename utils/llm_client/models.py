"""Value objects shared across the llm_client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class CacheSegment:
    """One text segment of a prompt; ``cache`` places a cache_control breakpoint after it."""

    text: str
    cache: bool = False


PromptInput = str | Sequence["CacheSegment"]


@dataclass(frozen=True)
class BatchRequest:
    """One prompt submitted as part of a provider batch job.

    ``response_schema`` (a JSON Schema dict) turns on provider structured output
    so the response is guaranteed to be schema-valid JSON — used with models
    whose reasoning would otherwise leak into or truncate a free-form reply.
    ``system`` / ``user`` accept ``CacheSegment`` lists exactly like the sync
    calls: Anthropic turns ``cache=True`` segments into prompt-cache breakpoints.
    """

    request_id: str
    system: PromptInput
    user: PromptInput
    task: str = ""
    response_schema: dict[str, Any] | None = None


QuestionKind = Literal["choice", "noul", "score"]


@dataclass(frozen=True)
class Question:
    """One typed question a ``Classifier`` answers about a state (contract C4).

    ``kind`` decides what ``options`` holds:

    - ``noul``: ``text`` is a yes/no question or a statement; no ``options``.
    - ``choice``: ``options`` are the labels, exactly one of which is picked.
    - ``score``: ``options`` are the ordered level descriptions of the scale,
      lowest first (describe situations, not numbers).
    """

    id: str
    kind: QuestionKind
    text: str
    options: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Answer:
    """The answer to the ``Question`` with the same ``id`` (contract C4).

    ``probabilities`` is a ``{option: probability}`` dict for ``choice``; for
    ``noul`` it is the probability of "yes"; for ``score`` it is the position on
    the scale normalised to 0.0 (first level) .. 1.0 (last level).
    """

    id: str
    probabilities: dict[str, float] | float


class BatchUnfinished(RuntimeError):
    """A resumable batch ended with items that never ran (``expired`` or ``canceled``). The
    answered items are cached, so the next call submits only these."""

    def __init__(self, request_ids: list[str]) -> None:
        super().__init__(
            f"{len(request_ids)} batch requests did not finish (expired or canceled); the "
            "answered ones are cached and a rerun submits only the unfinished ones"
        )
        self.request_ids = request_ids


class BatchGone(RuntimeError):
    """A saved batch can no longer be retrieved (unknown id, or its results are gone)."""

    def __init__(self, batch_id: str, cause: BaseException) -> None:
        super().__init__(
            f"batch {batch_id} can no longer be retrieved ({cause!r}); its saved id was "
            "dropped and a rerun submits a new batch"
        )
        self.batch_id = batch_id


class ClassifierError(RuntimeError):
    """A classifier request failed. ``index`` is the failing item's position in
    the ``classify_many`` call, or ``None`` for a single ``classify``."""

    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index
