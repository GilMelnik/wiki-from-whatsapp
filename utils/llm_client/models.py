"""Value objects shared across the llm_client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class GroundedCitation:
    title: str
    url: str


@dataclass(frozen=True)
class GroundedResult:
    text: str
    citations: tuple[GroundedCitation, ...] = ()
    search_queries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the on-disk cache. The ``grounded`` marker is what
        distinguishes a grounded entry from a plain text completion on read."""

        return {
            "grounded": True,
            "response": self.text,
            "citations": [{"title": c.title, "url": c.url} for c in self.citations],
            "search_queries": list(self.search_queries),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroundedResult":
        citations = tuple(
            GroundedCitation(title=c["title"], url=c["url"])
            for c in data.get("citations", [])
            if c.get("url")
        )
        return cls(
            text=data.get("response", ""),
            citations=citations,
            search_queries=tuple(data.get("search_queries", [])),
        )


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


class ClassifierError(RuntimeError):
    """A classifier request failed. ``index`` is the failing item's position in
    the ``classify_many`` call, or ``None`` for a single ``classify``."""

    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index
