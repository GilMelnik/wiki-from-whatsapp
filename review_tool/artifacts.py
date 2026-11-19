"""The artifact dependency graph: which file each step produces and what it was built from.

A node is one pipeline artifact: its key, its file (or original/edited pair), the step that
produces it and the keys of the artifacts that step reads. ``GRAPH`` is seeded with today's
pipeline in order; a task that introduces a new artifact adds one ``ArtifactNode`` to
``_pipeline_nodes`` at the right position (upstream keys must already be listed above it).

Staleness is purely mtime-based: an artifact is stale because of an upstream key when that
upstream's effective file (edited copy if present, else original) is newer than this
artifact's original. A ``curated`` artifact (the wiki plan: the owner's, re-made only on
request) keeps its upstream keys for job ordering but is never stale: re-running its step
would not change it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from review_tool.fs import FileStamp, stamp
from utils.paths import (
    ASSIGNMENTS,
    CHAT_ANDROID_PATH,
    CLAIMS,
    CLASSIFIED,
    DOCS_DIR,
    ENTITIES,
    ENTITY_TAG_DECISIONS_PATH,
    GOLD_THREADS_PATH,
    PLAN,
    PROOFREAD_RECORD,
    SAME_COMPLAINT_PATH,
    SAME_COMPLAINT_VETOES_PATH,
    STEP_1,
    STEP_2,
    STEP_3,
    STEP_4,
    STEP_5,
    STEP_6,
    STEP_7,
    STEP_8,
    STEP_9,
    STEP_10,
    STEP_11,
    SUGGESTIONS,
    TAGGED_MEMBERS_PATH,
    THREADS,
    WIKI_INDEX_FILE,
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
    curated: bool = False

    @classmethod
    def from_pair(
        cls,
        key: str,
        title: str,
        pair: Artifact,
        step: str,
        upstream: tuple[str, ...],
        *,
        curated: bool = False,
    ) -> ArtifactNode:
        """Node for a reviewed artifact (``utils.paths.Artifact`` original/edited pair)."""
        return cls(key, title, pair.original, pair.edited, step, upstream, curated)

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
        if original is not None and not node.curated:
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
        ArtifactNode("export", "Chat export", CHAT_ANDROID_PATH),
        ArtifactNode.from_pair("threads", "Conversations", THREADS, STEP_1, ("export",)),
        # Hand-tagged, frozen on purpose (``evaluate --freeze-gold``): no step, never stale.
        ArtifactNode("gold_threads", "Gold conversations (hand-tagged)", GOLD_THREADS_PATH),
        ArtifactNode.from_pair("classified", "Classified conversations", CLASSIFIED, STEP_2, ("threads",)),
        # The owner's word on which tagged members are providers (the Tagged members page): no
        # step; step 3 shows their tags by it, so a change makes the claims stale.
        ArtifactNode("tagged_members", "Tagged members", TAGGED_MEMBERS_PATH),
        ArtifactNode.from_pair(
            "claims", "Claims", CLAIMS, STEP_3, ("threads", "classified", "tagged_members")
        ),
        ArtifactNode.from_pair("entities", "Entities", ENTITIES, STEP_4, ("claims",)),
        # Steps 5 to 11 read the claim records, joined on read from the claims and the entities
        # (``step_5_same_complaint.claim_records``): they depend on those two nodes.
        # The same-complaint check (paid): which lone complaints about a named thing a second
        # person's same complaint releases. It reads the claim records (and step 3's audit).
        ArtifactNode(
            "same_complaint", "Same-complaint answers", SAME_COMPLAINT_PATH,
            step=STEP_5, upstream=("claims", "entities"),
        ),
        # The owner's "keep withheld" marks (the Withheld page): no step; with the check's
        # answers they decide which claims the pages may use, so a change makes the pages stale.
        ArtifactNode("withheld_vetoes", "Claims kept withheld", SAME_COMPLAINT_VETOES_PATH),
        # Curated: a normal step-6 run keeps the owner's plan as is, so new claims never make
        # it stale; the assignment step places them.
        ArtifactNode.from_pair("plan", "Wiki plan", PLAN, STEP_6, ("claims", "entities"), curated=True),
        ArtifactNode.from_pair(
            "assignments", "Claims assigned to pages", ASSIGNMENTS, STEP_7,
            ("claims", "entities", "plan"),
        ),
        ArtifactNode.from_pair(
            "pages", "Wiki pages", WIKI_PAGES, STEP_8,
            ("claims", "entities", "plan", "assignments", "same_complaint", "withheld_vetoes"),
        ),
        # The whole-wiki review's suggestions (a human gate: the owner decides on each one).
        ArtifactNode.from_pair(
            "suggestions", "Review suggestions", SUGGESTIONS, STEP_9, ("plan", "pages")
        ),
        # The Hebrew language edit after the owner's decisions: it rewrites the pages' edited
        # file; its record stands for the pass (stale when a page changed since).
        ArtifactNode(
            "proofread", "Proofreading", PROOFREAD_RECORD, step=STEP_10,
            upstream=("claims", "entities", "plan", "assignments", "pages"),
        ),
        # The owner's covered / make-a-tag decisions on the Plan page (hand-made, no step).
        ArtifactNode("entity_tag_decisions", "Entity tag decisions", ENTITY_TAG_DECISIONS_PATH),
        # The published site (docs/ + mkdocs.yml); its index file stands for the whole folder.
        ArtifactNode(
            "site", "Site", DOCS_DIR / WIKI_INDEX_FILE, step=STEP_11,
            upstream=("plan", "pages", "claims", "entities", "threads", "entity_tag_decisions"),
        ),
    )


GRAPH = ArtifactGraph(_pipeline_nodes())
