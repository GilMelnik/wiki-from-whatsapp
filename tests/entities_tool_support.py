"""Synthetic claims / entities and a TestClient for the entity review tool (temp dirs only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.tools.entities.tool import EntitiesTool
from step_4_entities.collect import ClaimCorpus
from step_4_entities.mentions import SimpleAnalyzer, analyze_claims, build_or_load_analysis
from step_4_entities.records import claim_entity_index, new_entity
from tests.review_tool_support import BASE_URL, build_app
from utils.json_io import write_json_file

SHORT_LOCK_TIMEOUT = 0.2
API = "/api/entities"

DS1, DS2, DS3, DS4, DS5, DS6 = (f"t-A-000{i}" for i in range(1, 7))

CLAIMS = [
    (
        DS1,
        "David Shield sells travel insurance, mail joindavidshield@davidshield.com",
        ["David Shield"],
    ),
    (DS2, "David Shield works with ORM", ["David Shield", "ORM"]),
    (DS3, "DavidShield is cheap", ["DavidShield"]),
    (DS4, "ORM office in Tel Aviv, write to maybe@shared.com", ["ORM", "Tel Aviv"]),
    (DS5, "David Shield is great", ["ORM"]),
    (DS6, "combine the morning plan with David Shield", ["Great Morning", "David Shield"]),
]


def claim(claim_id: str, text: str, entities: list[str]) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.rsplit("-", 1)[0],
        "claim_text": text,
        "entities": entities,
        "stance": "factual",
    }


def claims_payload(extra: list[dict[str, Any]] = ()) -> dict[str, Any]:
    return {"claims": [claim(*row) for row in CLAIMS] + list(extra)}


def build_entity_graph(root: Path) -> ArtifactGraph:
    """claims -> entities, plus the hand-made claim aggregations file (as in the pipeline)."""
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
            ArtifactNode("claim_aggregations", "Aggregations", root / "claim_aggregations.json"),
        ]
    )


def entities_payload(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Three entities: a two-name suggestion (one name attached by an update), an accepted
    one, and an ambiguous one flagged against the first."""
    corpus = ClaimCorpus(claims, analyze_claims(claims, SimpleAnalyzer()))

    def member(name: str, **extra: Any) -> dict[str, Any]:
        return {**corpus.member(name, corpus.claims_for(name)), **extra}

    attached = {"matched": "David Shield", "similarity": 0.91, "signals": ["string"]}
    entities = [
        new_entity(
            "e0000",
            [member("David Shield"), member("DavidShield", attached_by=attached)],
            merge_signals=["string"],
        ),
        new_entity("e0001", [member("ORM")], status="accepted"),
        new_entity(
            "e0002", [member("Tel Aviv")], status="ambiguous", conflict_with=["David Shield"]
        ),
    ]
    return {
        "entities": entities,
        "claim_entities": claim_entity_index(entities),
        "metadata": {"last_entity_number": 2},
    }


class EntitiesSetup:
    def __init__(
        self,
        root: Path,
        *,
        with_claims: bool = True,
        with_entities: bool = True,
        with_cache: bool = True,
        extra_claims: list[dict[str, Any]] = (),
    ) -> None:
        self.root = root
        self.graph = build_entity_graph(root)
        self.claims_path = self.graph.node("claims").original
        node = self.graph.node("entities")
        self.original, self.edited = node.original, node.edited
        self.aggregations = self.graph.node("claim_aggregations").original
        self.cache = root / "entity_claim_analysis.json"
        claims = claims_payload(extra_claims)["claims"]
        if with_claims:
            write_json_file({"claims": claims}, self.claims_path)
        if with_cache:
            build_or_load_analysis(claims, SimpleAnalyzer(), cache_path=self.cache)
        if with_entities:
            write_json_file(entities_payload(claims), self.original)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        """A fresh tool (new store handle) on the same files, as after a server restart."""
        tool = EntitiesTool(self.graph, analysis_path=self.cache, lock_timeout=SHORT_LOCK_TIMEOUT)
        app = build_app(self.root, specs=(tool.spec,), graph=self.graph)
        return TestClient(app, base_url=BASE_URL)

    def get(self, path: str, expect: int = 200, **params: Any) -> Any:
        response = self.client.get(f"{API}{path}", params=params)
        assert response.status_code == expect, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, expect: int = 200) -> Any:
        response = self.client.post(f"{API}{path}", json=body)
        assert response.status_code == expect, response.text
        return response.json()

    def delete(self, path: str, expect: int = 200, **params: Any) -> Any:
        response = self.client.delete(f"{API}{path}", params=params)
        assert response.status_code == expect, response.text
        return response.json()

    def entity(self, entity_id: str) -> dict[str, Any]:
        return self.get(f"/entities/{entity_id}")["entity"]

    def member(self, entity_id: str, name: str) -> dict[str, Any]:
        return next(m for m in self.entity(entity_id)["members"] if m["name"] == name)

    def shown_claims(self, entity_id: str, name: str) -> list[str]:
        """Every visible claim of the name (the detail itself carries only the first few)."""
        page = self.get(f"/entities/{entity_id}/claims", name=name, limit=1000)
        return [c["claim_id"] for c in page["claims"]]

    def saved(self) -> dict[str, Any]:
        return json.loads(self.edited.read_text(encoding="utf-8"))

    def saved_entity(self, entity_id: str) -> dict[str, Any]:
        return next(e for e in self.saved()["entities"] if e["entity_id"] == entity_id)
