"""The step registry: which pipeline step brings each artifact up to date, and whether the
review tool may start it.

One row per step that produces an artifact of ``review_tool.artifacts.GRAPH``. A row names the
artifact key it produces (the step id is that node's ``step``), a Hebrew title, the module
behind ``python -m <module>`` (``None`` while the step has no command line), and two flags:

- ``incremental_safe``: the step follows contract C14 (it reprocesses only new and changed
  upstream items and keeps manual edits), so one click in the review tool cannot destroy
  work. Only these steps can be started from the tool; for every other step the stale notice
  says to run it by hand. A step task that migrates its step to C14 flips this flag in its row.
- ``paid``: a run makes paid API calls worth asking about first (the tool shows a
  confirmation dialog).

``SERVED_STEPS`` is the registry the server uses.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from review_tool.artifacts import GRAPH, ArtifactGraph

REBUILD_FLAG = "--rebuild"


@dataclass(frozen=True)
class StepSpec:
    produces: str
    title: str
    module: str | None
    incremental_safe: bool = False
    paid: bool = False


STEP_SPECS: tuple[StepSpec, ...] = (
    StepSpec("threads", "שלב 1 · פיצול לשיחות", None),
    StepSpec("classified", "שלב 2 · סיווג שיחות", "step_2_classify.run", incremental_safe=True),
    StepSpec("claims", "שלב 3 · חילוץ טענות", "step_3_extract.run", incremental_safe=True, paid=True),
    StepSpec("entities", "שלב 4 · זיהוי ישויות", "step_4_entities.run", incremental_safe=True),
    StepSpec("groups", "שלב 5 · קיבוץ טענות", "step_5_aggregate.run"),
    StepSpec("plan", "שלב 6 · תכנון הוויקי", "step_6_plan.run", paid=True),
    StepSpec("pages", "שלב 7 · כתיבת הדפים", "step_7_write_pages.run", paid=True),
)


@dataclass(frozen=True)
class Step:
    """A registry row resolved against the artifact graph (``upstream``: what the step reads)."""

    key: str
    spec: StepSpec
    upstream: tuple[str, ...]

    def command(self, *, rebuild: bool = False) -> list[str]:
        """The subprocess command: this interpreter, ``-m <module>``, optionally ``--rebuild``."""
        if self.spec.module is None:
            raise ValueError(f"step {self.key!r} has no command line")
        return [sys.executable, "-m", self.spec.module, *([REBUILD_FLAG] if rebuild else [])]

    def to_json(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "key": self.key,
            "title": spec.title,
            "produces": spec.produces,
            "incremental_safe": spec.incremental_safe,
            "paid": spec.paid,
            "command": self._shown(rebuild=False),
            "rebuild_command": self._shown(rebuild=True),
        }

    def _shown(self, *, rebuild: bool) -> str | None:
        """The command as a user would type it (``python`` instead of this interpreter's path)."""
        if self.spec.module is None:
            return None
        return " ".join(["python", *self.command(rebuild=rebuild)[1:]])


class StepRegistry:
    """The rows by step id, checked against the graph when built."""

    def __init__(self, specs: Iterable[StepSpec], graph: ArtifactGraph) -> None:
        self._steps: dict[str, Step] = {}
        for spec in specs:
            step = self._resolve(spec, graph)
            if step.key in self._steps:
                raise ValueError(f"step {step.key!r} is listed twice in the step registry")
            self._steps[step.key] = step

    @staticmethod
    def _resolve(spec: StepSpec, graph: ArtifactGraph) -> Step:
        node = graph.node(spec.produces)
        if node.step is None:
            raise ValueError(
                f"step registry row {spec.title!r}: artifact {spec.produces!r} has no producing "
                "step in the artifact graph"
            )
        if spec.incremental_safe and spec.module is None:
            raise ValueError(
                f"step {node.step!r} is marked incremental_safe but has no module to run"
            )
        return Step(node.step, spec, node.upstream)

    @property
    def steps(self) -> tuple[Step, ...]:
        return tuple(self._steps.values())

    def step(self, key: str) -> Step:
        try:
            return self._steps[key]
        except KeyError:
            raise KeyError(f"unknown step {key!r}; known steps: {list(self._steps)}") from None


SERVED_STEPS = StepRegistry(STEP_SPECS, GRAPH)
