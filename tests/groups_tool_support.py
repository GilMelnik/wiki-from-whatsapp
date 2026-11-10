"""Synthetic step-5 data and a TestClient for the claim-groups review tool (temp dirs only).

The corpus (see ``tests.aggregate_fixtures``: claims in one plane have cosine cos(angle
difference), other planes 0) gives, after a real step-5 run with the fake embedder:

- ``A`` = a1, a2, a3 (Alpha, very close): one group; people p1 (backs a1, later disputes a2), p2
- ``S`` = s1 / s2 (Beta, cosine 0.93): two groups, a merge suggestion (margin -0.02)
- ``T`` = t1 / t2 (no entity, cosine 0.935): two groups, a weaker suggestion (margin -0.025)
- ``O`` = o1 (positive) / o2 (negative) (Gamma, cosine 0.95): never suggested, but contradicts
- ``d1`` and ``c1``: single claims far from everything
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

import step_5_aggregate.reviewer.store as store_module
import step_5_aggregate.similarity as similarity
from review_tool.artifacts import ArtifactGraph, ArtifactNode
from step_5_aggregate.reviewer.suggestions import EmbeddingsMissing
from step_5_aggregate.reviewer.tool import GroupsTool
from step_5_aggregate.run import run
from tests.aggregate_fixtures import Corpus, angle_for
from tests.review_tool_support import BASE_URL, build_app

SHORT_LOCK_TIMEOUT = 0.2
API = "/api/groups"
CLOSE = angle_for(0.995)


def build_corpus(root: Path) -> Corpus:
    corpus = Corpus(root)
    corpus.add("a1", entities=["Alpha"], date="2024-01-01", supporters=[("p1", "2024-01-01")])
    corpus.add(
        "a2",
        angle=CLOSE,
        entities=["Alpha"],
        date="2024-02-01",
        supporters=[("p2", "2024-02-01")],
        opposers=[("p1", "2024-03-01")],
    )
    corpus.add("a3", angle=-CLOSE, entities=["Alpha"], date="2024-02-15")
    corpus.add("s1", plane=1, entities=["Beta"], date="2024-01-02", supporters=[("p3", "2024-01-02")])
    corpus.add("s2", plane=1, angle=angle_for(0.93), entities=["Beta"], date="2024-05-01")
    corpus.add("t1", plane=2, date="2024-01-03")
    corpus.add("t2", plane=2, angle=angle_for(0.935), date="2024-06-01")
    corpus.add("o1", plane=3, stance="positive", entities=["Gamma"], date="2024-01-04")
    corpus.add(
        "o2", plane=3, angle=angle_for(0.95), stance="negative", entities=["Gamma"], date="2024-07-01"
    )
    corpus.add("d1", plane=4, date="2024-03-01", supporters=[("p4", "2024-03-01")])
    corpus.add("c1", plane=5, date="2024-04-01")
    return corpus


def build_groups_graph(root: Path, corpus: Corpus) -> ArtifactGraph:
    """claims -> entities -> groups, at the paths the corpus writes."""
    return ArtifactGraph(
        [
            ArtifactNode("claims", "Claims", root / "claims.json", root / "claims_edited.json", "s3"),
            ArtifactNode(
                "entities",
                "Entities",
                root / "entities.json",
                root / "entities_edited.json",
                "s4",
                ("claims",),
            ),
            ArtifactNode(
                "groups",
                "Groups",
                corpus.artifact.original,
                corpus.artifact.edited,
                "s5",
                ("claims", "entities"),
            ),
        ]
    )


class GroupsSetup:
    """The corpus on disk, step 5 run on it, and a client of the groups tool."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch, *, run_step: bool = True) -> None:
        self.root = root
        self.monkeypatch = monkeypatch
        self.corpus = build_corpus(root)
        monkeypatch.setattr(similarity, "embed_texts", self.corpus.embed)
        self.use_cache(self.corpus.embed)
        self.graph = build_groups_graph(root, self.corpus)
        self.original, self.edited = self.corpus.artifact.original, self.corpus.artifact.edited
        if run_step:
            self.run_step()
        self.client = self.new_client()

    def use_cache(self, embed: Any) -> None:
        """The tool's cache-only vectors: the fake embedder (the cache holds every text)."""
        self.monkeypatch.setattr(store_module, "cached_vectors", lambda texts: embed(texts, "similarity"))

    def cache_misses(self, count: int) -> None:
        def missing(texts: list[str]) -> np.ndarray:
            raise EmbeddingsMissing(count)

        self.monkeypatch.setattr(store_module, "cached_vectors", missing)

    def run_step(self) -> dict[str, Any]:
        """The real step 5 on the corpus files (rewrites the inputs first)."""
        return run(**self.corpus.paths())

    def new_client(self) -> TestClient:
        """A fresh tool (new store handle) on the same files, as after a server restart."""
        tool = GroupsTool(
            self.graph, audit_path=self.root / "audit.json", lock_timeout=SHORT_LOCK_TIMEOUT
        )
        app = build_app(self.root, specs=(tool.spec,), graph=self.graph)
        return TestClient(app, base_url=BASE_URL)

    def get(self, path: str, status: int = 200, **params: Any) -> Any:
        response = self.client.get(f"{API}{path}", params=params)
        assert response.status_code == status, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, status: int = 200) -> Any:
        response = self.client.post(f"{API}{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def delete(self, path: str, status: int = 200) -> Any:
        response = self.client.delete(f"{API}{path}")
        assert response.status_code == status, response.text
        return response.json()

    def payload(self, path: Path | None = None) -> dict[str, Any]:
        """The groups file (default: the edited copy if present, else the original)."""
        path = path or (self.edited if self.edited.exists() else self.original)
        return json.loads(path.read_text(encoding="utf-8"))

    def group_of(self, claim_id: str, path: Path | None = None) -> dict[str, Any]:
        return next(g for g in self.payload(path)["groups"] if claim_id in g["member_claim_ids"])

    def gid(self, claim_id: str) -> str:
        return self.group_of(claim_id)["group_id"]
