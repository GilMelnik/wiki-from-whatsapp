"""Measure step 4 against the human-reviewed entities of the old pipeline (numbers only).

``python -m step_4_entities.evaluate [--incremental]``

Reads, never writes, ``entities.eval_reviewed`` (the reviewed ``entities_edited.json`` of the
backup). Entities are compared by their member strings: two strings "belong together" when
the reviewer left them in one entity (a string the reviewer split across entities counts for
the entity holding most of its claims). Reported: how many reviewed multi-member entities
are reproduced exactly, split, or merged with other strings, and pairwise precision / recall.

``--incremental`` runs the 80 % / 20 % experiment in ``<data>/step_4_entities/eval/``:
cluster the earliest ``entities.eval_base_fraction`` of the claims (by first date), apply a
handful of manual edits through the reviewer store (merge, split, rename, exclusion,
rejected and manual contacts, deleted entity, accepted status), fold in the remaining claims
incrementally, then check that every edit survived and where the new strings landed. A full
clustering of all claims is the reference.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from step_4_entities.collect import load_claims_for_entities, tagged_names
from step_4_entities.records import explicit_member_claims, member_names
from review_tool.tools.entities.store import EntityStore, EntityView
from step_4_entities.run import run
from utils.config import ENTITIES_CONFIG
from utils.json_io import read_json_file
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import ENTITIES, ENTITY_ANALYSIS_PATH, STEP_4, Artifact, resolve, step_path

EVAL_CONFIG = ENTITIES_CONFIG["eval"]
logger = logging.getLogger(STEP_4)


def name_labels(payload: dict[str, Any]) -> dict[str, str]:
    """``name -> entity_id`` of the entity holding most of that name's claims."""

    best: dict[str, tuple[str, int]] = {}
    for entity in payload.get("entities") or []:
        for member in entity.get("members") or []:
            count = member.get("count") or 0
            if member["name"] not in best or count > best[member["name"]][1]:
                best[member["name"]] = (entity["entity_id"], count)
    return {name: label for name, (label, _) in best.items()}


def _groups(labels: dict[str, str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for name, label in labels.items():
        out[label].add(name)
    return out


def _pairs(groups: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {
        (a, b) for names in groups.values() for a in names for b in names if a < b
    }


def compare(predicted: dict[str, str], reviewed: dict[str, str]) -> dict[str, Any]:
    """Partition agreement on the strings both sides know."""

    shared = set(predicted) & set(reviewed)
    pred = _groups({n: predicted[n] for n in shared})
    gold = _groups({n: reviewed[n] for n in shared})
    exact = split = merged = 0
    multi = [names for names in gold.values() if len(names) > 1]
    for names in multi:
        labels = {predicted[n] for n in names}
        exact += len(labels) == 1 and pred[next(iter(labels))] == names
        split += len(labels) > 1
        merged += any(pred[label] - names for label in labels)
    pp, gp = _pairs(pred), _pairs(gold)
    tp = len(pp & gp)
    return {
        "strings": len(shared),
        "reviewed_multi_member": len(multi),
        "reproduced_exactly": exact,
        "split": split,
        "merged_with_other_strings": merged,
        "predicted_pairs": len(pp),
        "true_pairs": tp,
        "wrong_pairs": len(pp - gp),
        "missed_pairs": len(gp - pp),
        "precision": round(tp / len(pp), 4) if pp else 1.0,
        "recall": round(tp / len(gp), 4) if gp else 1.0,
        "impure_entities": sum(1 for names in pred.values() if len({reviewed[n] for n in names}) > 1),
    }


class IncrementalExperiment:
    """The 80 % / 20 % experiment; every artifact lives in ``work_dir``."""

    def __init__(self, claims: list[dict[str, Any]], reviewed: dict[str, str], work_dir: Path) -> None:
        self.reviewed = reviewed
        self.work = work_dir
        ordered = sorted(claims, key=lambda c: (c.get("first_date") or "", c["claim_id"]))
        cut = int(len(ordered) * EVAL_CONFIG["base_fraction"])
        self.base, self.rest = ordered[:cut], ordered[cut:]
        self.all = ordered
        self.artifact = Artifact(work_dir / "entities.json", work_dir / "entities_edited.json")
        self.expect: dict[str, Any] = {}

    def _run(self, name: str, claims: list[dict[str, Any]], artifact: Artifact) -> dict[str, Any]:
        path = self.work / f"{name}_claims.json"
        path.write_text(json.dumps({"claims": claims}, ensure_ascii=False), encoding="utf-8")
        cache = self.work / f"{artifact.original.stem}_analysis.json"
        if not cache.is_file() and ENTITY_ANALYSIS_PATH.is_file():
            shutil.copy2(ENTITY_ANALYSIS_PATH, cache)
        return run(claims_path=path, artifact=artifact, analysis_path=cache)

    def _store(self) -> EntityStore:
        return EntityStore(
            self.artifact.original,
            self.artifact.edited,
            self.work / "base_claims.json",
            self.work / "base_claims_edited.json",
            self.work / "claim_aggregations.json",
            self.work / f"{self.artifact.original.stem}_analysis.json",
        )

    def _edit(self) -> None:
        """A handful of reviewer edits chosen from the reviewed entities (deterministic)."""

        store = self._store()
        by_size = sorted(store.entities, key=lambda e: -sum(m["count"] for m in e["members"]))
        gold = self.reviewed
        used: set[str] = set()

        def free(entity: dict[str, Any]) -> bool:
            return entity["entity_id"] not in used

        owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entity in by_size:
            for label in {gold.get(m["name"]) for m in entity["members"]} - {None}:
                owners[label].append(entity)
        target, source = next((pair for pair in owners.values() if len(pair) == 2))[:2]
        merged = target["aliases"] + source["aliases"]
        store.merge(source["entity_id"], target["entity_id"])
        store.set_status(target["entity_id"], "accepted", EntityView())
        used |= {target["entity_id"], source["entity_id"]}
        self.expect["merge"] = (target["entity_id"], merged)

        impure = next(
            e for e in by_size
            if free(e) and len({gold.get(m["name"]) for m in e["members"]} - {None}) > 1
        )
        majority = gold.get(impure["members"][0]["name"])
        moving = [m["name"] for m in impure["members"] if gold.get(m["name"]) != majority]
        before = {e["entity_id"] for e in store.entities}
        store.move_member(impure["entity_id"], moving[0], None)
        split_id = next(e["entity_id"] for e in store.entities if e["entity_id"] not in before)
        for name in moving[1:]:
            store.move_member(impure["entity_id"], name, split_id)
        used |= {impure["entity_id"], split_id}
        self.expect["split"] = (impure["entity_id"], split_id, moving)

        renamed = next(e for e in by_size if free(e))
        new_name = f"{renamed['canonical_name']} (renamed)"
        store.rename(renamed["entity_id"], new_name)
        used.add(renamed["entity_id"])
        self.expect["rename"] = (renamed["entity_id"], new_name)

        excl = next(e for e in by_size if free(e) and e["members"][0]["count"] > 1)
        member = excl["members"][0]
        claim_id = explicit_member_claims(member)[0]
        store.exclude_claims(excl["entity_id"], member["name"], [claim_id])
        used.add(excl["entity_id"])
        self.expect["exclude"] = (excl["entity_id"], member["name"], claim_id)

        unc = next(e for e in by_size if free(e) and any((e.get("contacts_uncertain") or {}).values()))
        kind = next(k for k, v in unc["contacts_uncertain"].items() if v)
        value = unc["contacts_uncertain"][kind][0]
        store.resolve_contact(unc["entity_id"], kind, value, "reject")
        used.add(unc["entity_id"])
        self.expect["reject"] = (unc["entity_id"], kind, value)

        manual = next(e for e in by_size if free(e))
        store.set_contacts(manual["entity_id"], {"phone": ["000-0000"]})
        used.add(manual["entity_id"])
        self.expect["manual_contacts"] = manual["entity_id"]

        later = {n for c in self.rest for n in tagged_names(c)}
        doomed = next(
            e for e in reversed(by_size)
            if free(e) and len(e["members"]) == 1 and e["members"][0]["name"] in later
        )
        store.delete_entity(doomed["entity_id"], EntityView())
        self.expect["delete"] = doomed["members"][0]["name"]

    def _check(self, state: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
        entities = {e["entity_id"]: e for e in state["entities"]}
        names_of = {eid: {m["name"] for m in e["members"]} for eid, e in entities.items()}
        target, merged = self.expect["merge"]
        source, split, moving = self.expect["split"]
        renamed, new_name = self.expect["rename"]
        excl_id, excl_name, excl_claim = self.expect["exclude"]
        rej_id, kind, value = self.expect["reject"]
        excl_member = next(m for m in entities[excl_id]["members"] if m["name"] == excl_name)
        prior = {e["entity_id"]: {m["name"] for m in e["members"]} for e in before["entities"]}
        survived = {
            "merge_kept": set(merged) <= names_of[target],
            "accepted_kept": entities[target]["status"] == "accepted",
            "split_kept": set(moving) <= names_of[split] and not set(moving) & names_of[source],
            "rename_kept": entities[renamed]["canonical_name"] == new_name,
            "exclusion_kept": excl_claim in excl_member.get("excluded_claim_ids", [])
            and excl_claim not in excl_member["claim_ids"],
            "rejected_contact_kept": value not in entities[rej_id]["contacts_uncertain"][kind],
            "manual_contacts_kept": entities[self.expect["manual_contacts"]]["contacts"]["phone"]
            == ["000-0000"],
            "deleted_name_not_back": self.expect["delete"] not in member_names(state),
            "entity_ids_kept": sum(1 for eid in prior if eid in entities),
            "entity_ids_before": len(prior),
            "entities_lost_members": sum(1 for eid, names in prior.items() if not names <= names_of.get(eid, set())),
        }
        return survived

    def _landing(self, state: dict[str, Any], before: dict[str, Any]) -> dict[str, int]:
        old_names = member_names(before)
        old_labels = {self.reviewed.get(n) for n in old_names} - {None}
        counts: dict[str, int] = defaultdict(int)
        for entity in state["entities"]:
            for member in entity["members"]:
                name = member["name"]
                if name in old_names:
                    continue
                label = self.reviewed.get(name)
                mates = {self.reviewed.get(m["name"]) for m in entity["members"] if m["name"] != name}
                if label is None:
                    counts["new_string_not_reviewed"] += 1
                elif "attached_by" in member:
                    counts["attached_right" if label in mates else "attached_wrong"] += 1
                elif label in old_labels:
                    counts["new_entity_but_reviewer_has_it_in_old_entity"] += 1
                else:
                    counts["new_entity_right"] += 1
        return dict(counts)

    def run(self) -> dict[str, Any]:
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir(parents=True)
        base_run = self._run("base", self.base, self.artifact)
        self._edit()
        before = read_json_file(self.artifact.edited)
        update_run = self._run("all", self.all, self.artifact)
        state = read_json_file(self.artifact.edited)
        full = Artifact(self.work / "full.json", self.work / "full_edited.json")
        self._run("full", self.all, full)
        return {
            "base_claims": len(self.base),
            "new_claims": len(self.rest),
            "base_run": {k: base_run[k] for k in ("entity_count", "multi_member_count")},
            "update_run": {k: v for k, v in update_run.items() if k != "backup"},
            "edits_survived": self._check(state, before),
            "new_strings": self._landing(state, before),
            "incremental_vs_reviewed": compare(name_labels(state), self.reviewed),
            "full_vs_reviewed": compare(name_labels(read_json_file(full.edited)), self.reviewed),
        }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 4 evaluation against reviewed entities.")
    parser.add_argument("--incremental", action="store_true", help="also run the 80/20 experiment")
    args = parser.parse_args(argv)
    setup_step_logging(STEP_4)
    reviewed = name_labels(read_json_file(Path(EVAL_CONFIG["reviewed"])))
    logger.info(f"evaluate | git commit {git_commit()} | args {vars(args)} | config {ENTITIES_CONFIG}")
    logger.info(f"current entities vs reviewed: {compare(name_labels(read_json_file(resolve(ENTITIES))), reviewed)}")
    if args.incremental:
        claims = load_claims_for_entities()[0]
        result = IncrementalExperiment(claims, reviewed, step_path(STEP_4, "eval")).run()
        logger.info(f"incremental experiment: {json.dumps(result, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
