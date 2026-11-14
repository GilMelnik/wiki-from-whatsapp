"""Tool registry: what a review tool declares so the one server can host it.

A tool is a ``ToolSpec``: a name (also its ``?tool=`` value and its ``/api/<name>/`` prefix),
a title, a FastAPI router with paths relative to that prefix, and the artifact keys it reads
and writes (keys of ``review_tool.artifacts.GRAPH``). Registering a tool never touches its
files, so a tool whose artifacts do not exist yet still registers; ``GET /api/tools`` reports
what is missing. The tools the server hosts are listed in ``review_tool.toolset.TOOL_SPECS``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import APIRouter

_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")
RESERVED_NAMES = frozenset({"health", "tools", "artifacts", "events", "steps", "jobs"})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    router: APIRouter
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        """Add a tool. Its routes are served under ``/api/<spec.name>/``."""
        if not _NAME_PATTERN.fullmatch(spec.name):
            raise ValueError(
                f"tool name {spec.name!r} must be lowercase letters, digits, '-' or '_' "
                "and start with a letter"
            )
        if spec.name in RESERVED_NAMES:
            raise ValueError(
                f"tool name {spec.name!r} is reserved for the server's own /api/{spec.name}"
            )
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)
