"""Value objects shared across the llm_client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


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
    """

    request_id: str
    system: str
    user: str
    task: str = ""
    response_schema: dict[str, Any] | None = None
