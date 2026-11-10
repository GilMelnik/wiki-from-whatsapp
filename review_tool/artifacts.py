"""The artifact dependency graph: which file each step produces and what it was built from.

A node is one pipeline artifact: its key, its file (or original/edited pair), the step that
produces it and the keys of the artifacts that step reads. ``GRAPH`` is seeded with today's
pipeline in order; a task that introduces a new artifact adds one ``ArtifactNode`` to
``_pipeline_nodes`` at the right position (upstream keys must already be listed above it).

Staleness is purely mtime-based: an artifact is stale because of an upstream key when that
upstream's effective file (edited copy if present, else original) is newer than this
artifact's original.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from review_tool.fs import FileStamp, stamp
from utils.paths import (
    AGGREGATED,
    ASSIGNMENTS,
    CHAT_ANDROID_PATH,
    CLAIMS,
    CLASSIFIED,
    ENTITIES,
    GOLD_THREADS_PATH,
    MANUAL_AGGREGATIONS_PATH,
    PLAN,
    STEP_1,
    STEP_2,
    STEP_3,
    STEP_4,
    STEP_5,
    STEP_6,
    STEP_6_ASSIGN,
    STEP_7,
    THREADS,
    WIKI_PAGES,
    Artifact,
)

ORIGINAL = "original"
EDITED = "edited"


@dataclass(frozen=True)
class ArtifactNode:
    key: str
    title: str
    original: Path
    edited: Path | None = None
    step: str | None = None
    upstream: tuple[str, ...] = ()

    @classmethod
    def from_pair(
        cls, key: str, title: str, pair: Artifact, step: str, upstream: tuple[str, ...]
    ) -> ArtifactNode:
        """Node for a reviewed artifact (``utils.paths.Artifact`` original/edited pair)."""
        return cls(key, title, pair.original, pair.edited, step, upstream)

    @property
    def files(self) -> dict[str, Path]:
        """The node's files by role (``original`` and, when the node has one, ``edited``)."""
        files = {ORIGINAL: self.original}
        if self.edited is not None:
            files[EDITED] = self.edited
        return files

    @property
    def effective(self) -> Path:
        """The file the next step reads: the edited copy when it exists, else the original."""
        if self.edited is not None and self.edited.exists():
            return self.edited
        return self.original


@dataclass(frozen=True)
class ArtifactStatus:
    """What ``GET /api/artifacts`` reports for one node (timestamps are ISO-8601 UTC)."""

    key: str
    title: str
    step: str | None
    upstream: tuple[str, ...]
    has_edited_slot: bool
    exists: bool
    mtime: str | None
    edited_exists: bool
    edited_mtime: str | None
    stale_because: tuple[str, ...]


class ArtifactGraph:
    """Ordered, acyclic set of artifact nodes."""

    def __init__(self, nodes: Iterable[ArtifactNode] = ()) -> None:
        self._nodes: dict[str, ArtifactNode] = {}
        for node in nodes:
            self.add(node)

    def add(self, node: ArtifactNode) -> None:
        """Append ``node``. Its upstream keys must already be in the graph (keeps it acyclic)."""
        if node.key in self._nodes:
            raise ValueError(f"artifact key {node.key!r} is already in the graph")
        unknown = [key for key in node.upstream if key not in self._nodes]
        if unknown:
            raise ValueError(
                f"artifact {node.key!r} lists upstream keys that are not in the graph yet: "
                f"{unknown}; known keys: {list(self._nodes)}"
            )
        self._nodes[node.key] = node

    @property
    def nodes(self) -> tuple[ArtifactNode, ...]:
        return tuple(self._nodes.values())

    def node(self, key: str) -> ArtifactNode:
        try:
            return self._nodes[key]
        except KeyError:
            raise KeyError(
                f"unknown artifact key {key!r}; known keys: {list(self._nodes)}"
            ) from None

    def files(self, *keys: str) -> tuple[Path, ...]:
        """Every file (original and edited) of the given artifacts, for ``StoreHandle``."""
        return tuple(path for key in keys for path in self.node(key).files.values())

    def missing(self, keys: Iterable[str]) -> list[str]:
        """The given artifacts that have no file on disk yet (neither original nor edited)."""
        return [
            key
            for key in keys
            if not any(path.exists() for path in self.node(key).files.values())
        ]

    def status(self) -> list[ArtifactStatus]:
        """Existence, last change and staleness of every node, in pipeline order."""
        stamps = {
            node.key: {role: stamp(path) for role, path in node.files.items()}
            for node in self.nodes
        }
        return [self._node_status(node, stamps) for node in self.nodes]

    def _node_status(
        self, node: ArtifactNode, stamps: dict[str, dict[str, FileStamp | None]]
    ) -> ArtifactStatus:
        original = stamps[node.key][ORIGINAL]
        edited = stamps[node.key].get(EDITED)
        stale_because: list[str] = []
        if original is not None:
            for upstream_key in node.upstream:
                upstream = stamps[upstream_key]
                effective = upstream.get(EDITED) or upstream[ORIGINAL]
                if effective is not None and effective.mtime_ns > original.mtime_ns:
                    stale_because.append(upstream_key)
        return ArtifactStatus(
            key=node.key,
            title=node.title,
            step=node.step,
            upstream=node.upstream,
            has_edited_slot=node.edited is not None,
            exists=original is not None,
            mtime=original.iso if original else None,
            edited_exists=edited is not None,
            edited_mtime=edited.iso if edited else None,
            stale_because=tuple(stale_because),
        )


def _pipeline_nodes() -> tuple[ArtifactNode, ...]:
    """Today's pipeline, in order. Upstream = what the producing step actually reads."""
    return (
        ArtifactNode("export", "ייצוא הצ'אט", CHAT_ANDROID_PATH),
        ArtifactNode.from_pair("threads", "שיחות", THREADS, STEP_1, ("export",)),
        # Hand-tagged, frozen on purpose (``evaluate --freeze-gold``): no step, never stale.
        ArtifactNode("gold_threads", "שיחות זהב (תיוג ידני)", GOLD_THREADS_PATH),
        ArtifactNode.from_pair("classified", "סיווג שיחות", CLASSIFIED, STEP_2, ("threads",)),
        ArtifactNode.from_pair("claims", "טענות", CLAIMS, STEP_3, ("threads", "classified")),
        ArtifactNode.from_pair("entities", "ישויות", ENTITIES, STEP_4, ("claims",)),
        # Near-duplicate claims the owner groups by hand in the entity reviewer: no step.
        ArtifactNode("claim_aggregations", "איחוד טענות ידני", MANUAL_AGGREGATIONS_PATH),
        ArtifactNode.from_pair(
            "groups",
            "קבוצות טענות",
            AGGREGATED,
            STEP_5,
            ("claims", "entities", "claim_aggregations"),
        ),
        ArtifactNode.from_pair("plan", "תוכנית הויקי", PLAN, STEP_6, ("groups",)),
        ArtifactNode.from_pair(
            "assignments", "שיוך קבוצות לדפים", ASSIGNMENTS, STEP_6_ASSIGN, ("groups", "plan")
        ),
        ArtifactNode.from_pair("pages", "דפי הויקי", WIKI_PAGES, STEP_7, ("claims", "plan")),
    )


GRAPH = ArtifactGraph(_pipeline_nodes())
