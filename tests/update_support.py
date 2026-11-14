"""A synthetic pipeline for the update driver's tests: the real step registry and artifact
graph moved under ``tmp_path``, fake step runners that write their artifact, a fake estimator
and a fake review-server check. No step code, no API call, no real data."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from review_tool.artifacts import GRAPH, ArtifactGraph, ArtifactNode
from review_tool.steps import STEP_SPECS, Step, StepRegistry
from step_10_update.driver import Options, Outcome, UpdateDriver, UpdatePaths
from step_10_update.estimate import Estimate
from utils.config import DATA_DIR, PROJECT_ROOT
from utils.paths import STEP_3, STEP_6_ASSIGN, STEP_7, STEP_8, STEP_8_PROOFREAD

PAID_STEPS = frozenset({STEP_3, STEP_6_ASSIGN, STEP_7, STEP_8, STEP_8_PROOFREAD})


def message(msg_id: str, minute: int, day: int = 1) -> dict[str, Any]:
    return {"id": msg_id, "date": f"{day:02d}/03/2026", "time": f"10:{minute:02d}",
            "sender": {"user_name": "A", "phone": "0"}, "text": f"text {msg_id}",
            "quote": None, "reactions": []}


def old_chat() -> list[dict[str, Any]]:
    return [message(f"m{i}", i) for i in range(30)]


def newer_chat() -> list[dict[str, Any]]:
    return [*old_chat(), message("n1", 0, day=2), message("n2", 1, day=2)]


class Clock:
    """Strictly increasing file times, so mtime-based staleness never ties in a test."""

    def __init__(self) -> None:
        self.last = time.time_ns()

    def touch(self, path: Path) -> None:
        self.last = max(time.time_ns(), self.last + 1_000_000)
        os.utime(path, ns=(self.last, self.last))


def _moved(path: Path, root: Path) -> Path:
    if path.is_relative_to(DATA_DIR):
        return root / path.relative_to(DATA_DIR)
    return root / "repo" / path.relative_to(PROJECT_ROOT)


def rooted_graph(root: Path) -> ArtifactGraph:
    """The pipeline's artifact graph with every file under ``root``."""
    nodes: list[ArtifactNode] = []
    for node in GRAPH.nodes:
        edited = None if node.edited is None else _moved(node.edited, root)
        nodes.append(replace(node, original=_moved(node.original, root), edited=edited))
    return ArtifactGraph(nodes)


class World:
    """Input, graph, fake steps and the driver's paths under one temp folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.graph = rooted_graph(root)
        self.steps: list[Step] = list(StepRegistry(STEP_SPECS, self.graph).steps)
        self.paths = UpdatePaths(
            input=self.graph.node("export").original,
            updates_dir=root / "updates",
            state=root / "step_10_update" / "state.json",
            lease=root / "step_10_update" / "running.json",
        )
        self.clock = Clock()
        self.calls: list[str] = []
        self.runs = 0
        self.unchanged: set[str] = set()
        self.failing: set[str] = set()
        self.paid_calls = 2
        self.busy: str | None = None
        self.write_json(self.paths.input, old_chat())
        self.write_json(self.node("plan").original, {"pages": {}, "tree": [], "labels": {}})

    def node(self, key: str) -> ArtifactNode:
        return self.graph.node(key)

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.clock.touch(path)

    def export(self, messages: list[dict[str, Any]], name: str = "new_export.json") -> Path:
        path = self.root / "incoming" / name
        self.write_json(path, messages)
        return path

    def runner(self, step: Step) -> Any:
        def run() -> dict[str, Any]:
            self.calls.append(step.key)
            if step.key in self.failing:
                raise RuntimeError(f"{step.key} broke")
            self.runs += 1
            items = ["same"] if step.key in self.unchanged else [f"item {self.runs}"]
            self.write_json(self.node(step.spec.produces).original,
                            {"items": items, "metadata": {"run": self.runs}})
            return {"written": True, "items": len(items)}

        return run

    def estimate(self, step: Step) -> Estimate:
        return Estimate(step.key, self.paid_calls, 0.25 * self.paid_calls,
                        f"{self.paid_calls} calls", "test figures")

    def driver(self) -> UpdateDriver:
        return UpdateDriver(
            self.steps, self.graph, estimator=self.estimate, server_check=lambda: self.busy,
            paths=self.paths, runners={step.key: self.runner(step) for step in self.steps},
            logger=logging.getLogger("test_update"),
        )

    def run(self, export: Path | None = None, **options: Any) -> Outcome:
        return self.driver().run(export, Options(**options))

    def state(self) -> dict[str, Any]:
        return json.loads(self.paths.state.read_text(encoding="utf-8"))

    def snapshot(self) -> dict[str, tuple[int, bytes]]:
        """Every file under the root with its time and bytes (but the lease: a lock file)."""
        return {str(path): (path.stat().st_mtime_ns, path.read_bytes())
                for path in sorted(self.root.rglob("*"))
                if path.is_file() and path != self.paths.lease}
