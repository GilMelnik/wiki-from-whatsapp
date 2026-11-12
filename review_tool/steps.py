"""The step registry: which pipeline step brings each artifact up to date, and whether the
review tool may start it.

One row per step that produces an artifact of ``review_tool.artifacts.GRAPH``. A row names the
artifact key it produces (the step id is that node's ``step``), an English title, the module
behind ``python -m <module>`` (``None`` while the step has no command line), and two flags:

- ``incremental_safe``: the step follows contract C14 (it reprocesses only new and changed
  upstream items and keeps manual edits), so one click in the review tool cannot destroy
  work. Only these steps can be started from the tool; for every other step the stale notice
  says to run it by hand. A step task that migrates its step to C14 flips this flag in its row.
- ``paid``: a run makes paid API calls worth asking about first (the tool shows a
  confirmation dialog; the update driver runs it only when allowed, or when its estimate says
  it would make no paid call).
- ``review_tool``: the review tool where a person looks at the step's output before the
  pipeline goes on (``python -m review_tool --tool <name>``); the update driver stops there.

The rows are the one description of the pipeline: their order is the order the steps run in
(``pipeline.py`` and ``step_10_update`` walk them), ``Step.run_function`` is the step's own
``run()``. ``SERVED_STEPS`` is the registry the server uses.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import import_module
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
    review_tool: str | None = None


STEP_SPECS: tuple[StepSpec, ...] = (
    StepSpec("threads", "Step 1 · Split into conversations", "step_1_threads_split.run", incremental_safe=True),
    StepSpec("classified", "Step 2 · Classify conversations", "step_2_classify.run", incremental_safe=True),
    StepSpec(
        "claims", "Step 3 · Extract claims", "step_3_extract.run", incremental_safe=True,
        paid=True, review_tool="pii",
    ),
    StepSpec(
        "entities", "Step 4 · Find entities", "step_4_entities.run", incremental_safe=True,
        review_tool="entities",
    ),
    StepSpec(
        "groups", "Step 5 · Group claims", "step_5_aggregate.run", incremental_safe=True,
        review_tool="groups",
    ),
    StepSpec(
        "plan", "Step 6 · Plan the wiki", "step_6_plan.run", incremental_safe=True, paid=True,
        review_tool="plan",
    ),
    StepSpec(
        "assignments", "Step 6b · Assign groups to pages", "step_6_plan.assign",
        incremental_safe=True, paid=True, review_tool="plan",
    ),
    StepSpec("pages", "Step 7 · Write the pages", "step_7_write_pages.run", incremental_safe=True, paid=True),
    StepSpec(
        "suggestions", "Step 8 · Review the whole wiki", "step_8_review.run",
        incremental_safe=True, paid=True, review_tool="suggestions",
    ),
    StepSpec("site", "Step 9 · Build the site", "step_9_site.run", incremental_safe=True),
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

    def run_function(self) -> Callable[..., dict[str, Any]]:
        """The step's own ``run()`` (its module is imported only now: some are heavy)."""
        if self.spec.module is None:
            raise ValueError(f"step {self.key!r} has no module")
        return import_module(self.spec.module).run

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
