"""Step 4 reconcile (C14): incremental updates keep the reviewer's work and entity ids."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from step_4_entities.mentions import SimpleAnalyzer
from step_4_entities.reviewer.store import EntityStore
from step_4_entities.run import run
from utils.paths import Artifact


class CountingAnalyzer(SimpleAnalyzer):
    """Model-free analyzer that records how many texts it analyzed."""

    def __init__(self) -> None:
        self.calls = 0

    def analyze_batch(self, texts: Sequence[str]) -> list[list[dict]]:
        self.calls += len(texts)
        return super().analyze_batch(texts)


def _claim(claim_id: str, entities: list[str], text: str) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.rsplit("-", 1)[0],
        "claim_text": text,
        "entities": entities,
    }


BASE = [
    _claim("t1-a", ["Alpha Agency"], "Alpha Agency charges a fee for the process"),
    _claim("t1-b", ["עמית פלס"], "עמית פלס מלווה את התהליך"),
    _claim("t1-c", ["Amit Peles"], "Amit Peles guides the process"),
    _claim("t2-a", ["Delta Clinic"], "Delta Clinic does the medical tests"),
    _claim("t2-b", ["Delta"], "Delta does the medical tests quickly"),
    _claim("t3-a", ["Omega Lawyers"], "Omega Lawyers prepare the court order"),
    _claim("t4-a", ["Sigma Insurance"], "Sigma Insurance covers the newborn"),
    _claim("t5-a", ["Kappa"], "Kappa hospital delivery ward"),
]


class Step:
    """Runs step 4 on a private claims file and artifact inside ``tmp_path``."""

    def __init__(self, tmp_path: Path) -> None:
        self.claims_path = tmp_path / "claims.json"
        self.artifact = Artifact(tmp_path / "entities.json", tmp_path / "entities_edited.json")
        self.analysis_path = tmp_path / "analysis.json"
        self.seed_path = tmp_path / "no_seed.json"
        self.analyzer = CountingAnalyzer()

    def run(self, claims: list[dict], **kwargs: object) -> dict:
        self.claims_path.write_text(json.dumps({"claims": claims}), encoding="utf-8")
        return run(
            claims_path=self.claims_path,
            artifact=self.artifact,
            analyzer=self.analyzer,
            analysis_path=self.analysis_path,
            seed_path=self.seed_path,
            **kwargs,
        )

    def edited(self) -> dict:
        return json.loads(self.artifact.edited.read_text(encoding="utf-8"))

    def store(self) -> EntityStore:
        store = EntityStore(entities_path=self.artifact.edited, claims_path=self.claims_path)
        store.load()
        return store

    def entity_of(self, name: str) -> dict:
        found = [
            e for e in self.edited()["entities"] if any(m["name"] == name for m in e["members"])
        ]
        assert len(found) == 1, f"{name} is in {len(found)} entities"
        return found[0]


@pytest.fixture
def step(tmp_path: Path) -> Step:
    s = Step(tmp_path)
    s.run(list(BASE))
    return s


def test_first_run_clusters_and_indexes_claims(step: Step) -> None:
    data = step.edited()
    assert data == json.loads(step.artifact.original.read_text(encoding="utf-8"))
    amit = step.entity_of("Amit Peles")
    assert amit["entity_id"] == step.entity_of("עמית פלס")["entity_id"]  # transliteration
    assert step.entity_of("Delta")["entity_id"] == step.entity_of("Delta Clinic")["entity_id"]
    assert step.entity_of("Alpha Agency")["entity_id"] != amit["entity_id"]
    assert set(data["metadata"]["inputs"]) == {c["claim_id"] for c in BASE}
    assert data["claim_entities"]["t1-b"] == [amit["entity_id"]]
    assert all("topics" not in e for e in data["entities"])


def test_unchanged_claims_change_nothing_and_analyze_nothing(step: Step) -> None:
    before = step.artifact.edited.read_bytes()
    step.analyzer.calls = 0
    result = step.run(list(BASE))
    assert result["claims_processed"] == 0
    assert step.analyzer.calls == 0
    assert step.artifact.edited.read_bytes() == before


def test_new_claim_with_known_string_joins_its_entity(step: Step) -> None:
    delta_id = step.entity_of("Delta")["entity_id"]
    step.analyzer.calls = 0
    step.run([*BASE, _claim("t6-a", ["Delta"], "Delta opened a second branch")])
    assert step.analyzer.calls == 1  # only the new claim is analyzed
    delta = step.entity_of("Delta")
    assert delta["entity_id"] == delta_id
    member = next(m for m in delta["members"] if m["name"] == "Delta")
    assert "t6-a" in member["claim_ids"]
    assert delta_id in step.edited()["claim_entities"]["t6-a"]


def test_new_strings_attach_or_become_new_entities(step: Step) -> None:
    ids_before = {e["entity_id"] for e in step.edited()["entities"]}
    omega_id = step.entity_of("Omega Lawyers")["entity_id"]
    step.run(
        [
            *BASE,
            _claim("t6-a", ["Omega Lawyer"], "Omega Lawyer prepared the court order"),
            _claim("t7-a", ["Zeta Bank"], "Zeta Bank transfers the payment"),
        ]
    )
    omega = step.entity_of("Omega Lawyer")
    assert omega["entity_id"] == omega_id
    attached = next(m for m in omega["members"] if m["name"] == "Omega Lawyer")
    assert attached["attached_by"]["matched"] == "Omega Lawyers"
    zeta_id = step.entity_of("Zeta Bank")["entity_id"]
    assert zeta_id not in ids_before
    assert int(zeta_id[1:]) > max(int(i[1:]) for i in ids_before)


def test_removed_claims_shrink_and_drop_entities(step: Step) -> None:
    kappa_id = step.entity_of("Kappa")["entity_id"]
    kept = [c for c in BASE if c["claim_id"] not in {"t5-a", "t2-b"}]
    result = step.run(kept)
    assert result["entities_dropped"] == 1
    data = step.edited()
    assert kappa_id not in {e["entity_id"] for e in data["entities"]}
    delta = step.entity_of("Delta Clinic")
    # "Delta" is still mentioned in the text of t2-a, so only its t2-b pair goes.
    assert {m["name"]: m["claim_ids"] for m in delta["members"]} == {
        "Delta Clinic": ["t2-a"],
        "Delta": ["t2-a"],
    }
    assert "t5-a" not in data["claim_entities"] and "t2-b" not in data["claim_entities"]


def test_changed_claim_text_moves_its_pairs(step: Step) -> None:
    edited = _claim("t2-b", ["Sigma Insurance"], "Sigma Insurance also covers tests")
    step.run([edited if c["claim_id"] == "t2-b" else c for c in BASE])
    delta = step.entity_of("Delta Clinic")
    assert all("t2-b" not in m["claim_ids"] for m in delta["members"])
    sigma = step.entity_of("Sigma Insurance")
    assert "t2-b" in sigma["members"][0]["claim_ids"]


def test_manual_merge_split_rename_survive_an_update(step: Step) -> None:
    store = step.store()
    omega = step.entity_of("Omega Lawyers")["entity_id"]
    sigma = step.entity_of("Sigma Insurance")["entity_id"]
    store.merge(sigma, omega)  # manual merge of two unrelated-looking strings
    delta = step.entity_of("Delta")["entity_id"]
    store.move_member(delta, "Delta")  # manual split into a new entity
    split_id = step.entity_of("Delta")["entity_id"]
    alpha = step.entity_of("Alpha Agency")["entity_id"]
    store.rename_entity(alpha, "Alpha (agency)")
    store.set_status(alpha, "accepted")

    step.run(
        [
            *BASE,
            _claim("t6-a", ["Delta"], "Delta has a new lab"),
            _claim("t6-b", ["Alpha Agencies"], "Alpha Agencies runs webinars"),
        ]
    )
    assert step.entity_of("Sigma Insurance")["entity_id"] == omega
    assert step.entity_of("Omega Lawyers")["entity_id"] == omega
    assert step.entity_of("Delta")["entity_id"] == split_id != delta
    assert step.entity_of("Delta Clinic")["entity_id"] == delta
    assert "t6-a" in step.entity_of("Delta")["members"][0]["claim_ids"]
    renamed = step.entity_of("Alpha Agency")
    assert renamed["canonical_name"] == "Alpha (agency)"
    assert renamed["status"] == "accepted"
    assert step.entity_of("Alpha Agencies")["entity_id"] == alpha


def test_exclusion_contacts_and_deleted_entity_survive(tmp_path: Path) -> None:
    step = Step(tmp_path)
    claims = [
        *BASE,
        _claim("t8-a", ["Kappa"], "Kappa hospital phone 03-555-0101 for delivery"),
        _claim("t8-b", ["Kappa", "Sigma Insurance"], "Kappa and Sigma Insurance via help@k.org"),
    ]
    step.run(claims)
    store = step.store()
    kappa = step.entity_of("Kappa")["entity_id"]
    store.exclude_claims(kappa, "Kappa", ["t5-a"])
    store.resolve_uncertain_contact(kappa, kind="email", value="help@k.org", action="reject")
    store.set_contacts(step.entity_of("Delta")["entity_id"], {"phone": ["+972-3-000"]})
    store.delete_entity(step.entity_of("Omega Lawyers")["entity_id"])

    step.run(
        [
            *claims,
            _claim("t9-a", ["Kappa"], "Kappa again via help@k.org and Sigma Insurance"),
            _claim("t9-b", ["Omega Lawyers"], "Omega Lawyers again"),
            _claim("t9-c", ["Delta"], "Delta contact delta@d.com"),
        ]
    )
    kappa_entity = step.entity_of("Kappa")
    member = kappa_entity["members"][0]
    assert member["excluded_claim_ids"] == ["t5-a"]
    assert "t5-a" not in member["claim_ids"] and "t9-a" in member["claim_ids"]
    assert "help@k.org" not in kappa_entity["contacts_uncertain"]["email"]
    assert step.entity_of("Delta")["contacts"] == {
        "email": [],
        "phone": ["+972-3-000"],
        "website": [],
    }
    data = step.edited()
    assert "Omega Lawyers" in data["deleted_names"]
    assert not any(m["name"] == "Omega Lawyers" for e in data["entities"] for m in e["members"])


def test_only_restricts_the_work(step: Step) -> None:
    claims = [
        *BASE,
        _claim("t6-a", ["Zeta Bank"], "Zeta Bank transfers"),
        _claim("t7-a", ["Eta Fund"], "Eta Fund grants"),
    ]
    result = step.run(claims, only=["t6-a"])
    assert result["new_claims"] == 1
    data = step.edited()
    assert "t7-a" not in data["metadata"]["inputs"]
    assert "t1-a" in data["metadata"]["inputs"]
    step.entity_of("Zeta Bank")
    assert not any(m["name"] == "Eta Fund" for e in data["entities"] for m in e["members"])


def test_rebuild_backs_up_and_never_reuses_ids(step: Step) -> None:
    store = step.store()
    store.rename_entity(step.entity_of("Kappa")["entity_id"], "Renamed")
    last = step.edited()["metadata"]["last_entity_number"]
    result = step.run(list(BASE), rebuild=True)
    assert result["backup"] and Path(result["backup"]).is_file()
    data = step.edited()
    assert min(int(e["entity_id"][1:]) for e in data["entities"]) > last
    assert all(e["canonical_name"] != "Renamed" for e in data["entities"])


def test_store_ids_and_claim_index_follow_edits(step: Step) -> None:
    store = step.store()
    last = step.edited()["metadata"]["last_entity_number"]
    kappa = step.entity_of("Kappa")["entity_id"]
    store.delete_entity(kappa)
    sigma = step.entity_of("Sigma Insurance")["entity_id"]
    moved = store.move_member(sigma, "Sigma Insurance")  # into a brand-new entity
    assert int(moved["entity_id"][1:]) == last + 1  # never reuses the deleted id
    index = step.edited()["claim_entities"]
    assert index["t4-a"] == [moved["entity_id"]]
    assert "t5-a" not in index


def test_attachment_needs_every_member_to_match(step: Step) -> None:
    store = step.store()
    omega = step.entity_of("Omega Lawyers")["entity_id"]
    store.merge(step.entity_of("Sigma Insurance")["entity_id"], omega)
    step.run(
        [
            *BASE,
            _claim("t6-a", ["Sigma Insurances"], "Sigma Insurances covers twins"),
            _claim("t6-b", ["Kappas"], "Kappas hospital delivery ward"),
        ]
    )
    # Like "Sigma Insurance" but not like "Omega Lawyers": not attached to the merged entity.
    assert step.entity_of("Sigma Insurances")["entity_id"] != omega
    # Like the only member of an entity: attached.
    assert step.entity_of("Kappas")["entity_id"] == step.entity_of("Kappa")["entity_id"]
