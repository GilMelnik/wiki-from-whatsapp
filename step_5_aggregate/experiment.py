"""The 80 % / 20 % experiment: do manual edits survive an incremental run, and do new claims
land where a fresh grouping would put them? (``python -m step_5_aggregate.evaluate --incremental``)

In ``AGGREGATE_EVAL_DIR / "incremental"`` (wiped first): group the oldest
``aggregate.eval.base_fraction`` of the claims (by first date) with ``run``, edit the edited
file the way a reviewer would (merge two groups, split one, move a claim, choose another
representative), change one grouped claim's text and drop another claim upstream, then run
again on all claims. Reported: which edits survived, how many base group ids are kept, whether
every derived field matches a recount, where the new claims went (joined / opened) compared
with a from-scratch grouping of all claims, and that a third run writes nothing.
"""

from __future__ import annotations

import itertools
import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from step_5_aggregate.derive import people
from step_5_aggregate.groups import (
    Group,
    allocate_group_id,
    claim_order,
    group_from_scratch,
    used_group_ids,
)
from step_5_aggregate.inputs import load_inputs, load_must_links
from step_5_aggregate.run import run
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer
from utils.json_io import write_json_file
from utils.paths import AGGREGATE_EVAL_DIR, Artifact, resolve_claims_path

Pair = tuple[str, str]
FIRST_RUN_KEYS = ("group_count", "multi_member_group_count", "must_links_applied")
SECOND_RUN_KEYS = (
    "joined",
    "opened",
    "must_linked",
    "must_links_applied",
    "claims_left_groups",
    "groups_dropped",
    "group_count",
)


def pair_of(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def pairs_of(groups: Iterable[Iterable[str]]) -> set[Pair]:
    """Every unordered pair of claims that share a group."""

    return {pair_of(a, b) for members in groups for a, b in itertools.combinations(members, 2)}


def _multi(groups: list[Group], used: set[str], size: int = 2) -> Group:
    return next(
        g for g in groups if len(g["member_claim_ids"]) >= size and g["group_id"] not in used
    )


def apply_reviewer_edits(groups: list[Group]) -> dict[str, Any]:
    """Merge, split, move and representative edits in the shapes the reviewer store writes;
    returns what each edit should look like after the next run."""

    used: set[str] = set()
    keep = _multi(groups, used)
    used.add(keep["group_id"])
    gone = _multi(groups, used)
    used.add(gone["group_id"])
    keep["member_claim_ids"] += gone["member_claim_ids"]
    keep["merged_from"] = [
        *keep.get("merged_from", []), gone["group_id"], *gone.get("merged_from", [])
    ]
    groups.remove(gone)
    members = list(keep["member_claim_ids"])
    merge = {"group": keep["group_id"], "gone": gone["group_id"], "members": members}

    source = _multi(groups, used, 3)
    used.add(source["group_id"])
    part = source["member_claim_ids"][-1:]
    source["member_claim_ids"] = source["member_claim_ids"][:-1]
    split_id = allocate_group_id(part[0], used_group_ids(groups))
    groups.append({"group_id": split_id, "member_claim_ids": part})
    split = {"group": source["group_id"], "part": split_id, "claims": part}

    origin = _multi(groups, used)
    used.add(origin["group_id"])
    target = next(g for g in groups if g["group_id"] not in used)
    used.add(target["group_id"])
    moved = origin["member_claim_ids"].pop()
    target["member_claim_ids"].append(moved)
    move = {"claim": moved, "to": target["group_id"]}

    chosen = _multi(groups, used)
    used.add(chosen["group_id"])
    rep = next(c for c in chosen["member_claim_ids"] if c != chosen["representative_claim_id"])
    chosen["representative_claim_id"] = rep
    chosen["representative_manual"] = True
    return {
        "merge": merge,
        "split": split,
        "move": move,
        "representative": {"group": chosen["group_id"], "claim": rep},
        "touched": used | {split_id},
    }


def _survived(groups: list[Group], expected: dict[str, Any]) -> dict[str, bool]:
    by_id = {g["group_id"]: g for g in groups}
    group_of = {c: g["group_id"] for g in groups for c in g["member_claim_ids"]}
    merge, split, move, rep = (expected[k] for k in ("merge", "split", "move", "representative"))
    return {
        "merge": merge["gone"] not in by_id
        and all(group_of.get(c) == merge["group"] for c in merge["members"] if c in group_of)
        and merge["gone"] in by_id[merge["group"]].get("merged_from", []),
        "split": all(group_of.get(c) == split["part"] for c in split["claims"])
        and split["group"] in by_id,
        "move": group_of.get(move["claim"]) == move["to"],
        "representative": by_id[rep["group"]]["representative_claim_id"] == rep["claim"],
    }


def incremental_experiment() -> dict[str, Any]:
    folder = AGGREGATE_EVAL_DIR / "incremental"
    shutil.rmtree(folder, ignore_errors=True)
    artifact = Artifact(
        folder / "claims_aggregated.json", folder / "claims_aggregated_edited.json"
    )
    backup_dir = Path(AGGREGATE_CONFIG["eval"]["backup_dir"])
    links = backup_dir / "step_4_entities" / "claim_aggregations.json"
    payload = json.loads(resolve_claims_path().read_text("utf-8"))
    by_id = {c["claim_id"]: c for c in payload["claims"]}
    order = claim_order(by_id, by_id)
    cut = int(len(order) * float(AGGREGATE_CONFIG["eval"]["base_fraction"]))
    base_ids = set(order[:cut])
    base_path, full_path = folder / "claims_base.json", folder / "claims_all.json"
    base_claims = [c for c in payload["claims"] if c["claim_id"] in base_ids]
    write_json_file({**payload, "claims": base_claims}, base_path)
    first = run(claims_path=base_path, artifact=artifact, must_links_path=links)

    edited = json.loads(artifact.edited.read_text("utf-8"))
    base_group_ids = {g["group_id"] for g in edited["groups"]}
    expected = apply_reviewer_edits(edited["groups"])
    write_json_file(edited, artifact.edited)
    untouched = [g for g in edited["groups"] if g["group_id"] not in expected["touched"]]
    rewritten_group = next(g for g in untouched if len(g["member_claim_ids"]) > 1)
    rewritten = rewritten_group["member_claim_ids"][0]
    dropped_group = next(g for g in untouched if len(g["member_claim_ids"]) == 1)
    dropped = dropped_group["member_claim_ids"][0]
    full = {**payload, "claims": []}
    for claim in payload["claims"]:
        if claim["claim_id"] == dropped:
            continue
        if claim["claim_id"] == rewritten:
            suffix = AGGREGATE_CONFIG["eval"]["edit_suffix"]
            claim = {**claim, "claim_text": claim["claim_text"] + suffix}
        full["claims"].append(claim)
    write_json_file(full, full_path)
    second = run(claims_path=full_path, artifact=artifact, must_links_path=links)
    after = json.loads(artifact.edited.read_text("utf-8"))
    third = run(claims_path=full_path, artifact=artifact, must_links_path=links)

    inputs = load_inputs(full_path)
    group_of = {c: g["group_id"] for g in after["groups"] for c in g["member_claim_ids"]}
    recount_ok = all(
        {k: g[k] for k in ("supporter_count", "opposer_count", "by_month")}
        == people(g["member_claim_ids"], inputs.audit)
        for g in after["groups"]
    )
    scorer = ClaimScorer.embed(inputs.claims, inputs.claim_entities)
    fresh = pairs_of(group_from_scratch(inputs.claims, scorer, load_must_links(inputs, links)))
    new_ids = set(inputs.claims) - base_ids
    incremental = {
        p
        for p in pairs_of(g["member_claim_ids"] for g in after["groups"])
        if new_ids.intersection(p)
    }
    fresh_new = {p for p in fresh if new_ids.intersection(p)}
    # Gone on purpose: the merged-away group and the singleton of the claim removed upstream.
    kept = base_group_ids - {expected["merge"]["gone"], dropped_group["group_id"]}
    after_ids = {g["group_id"] for g in after["groups"]}
    return {
        "base_claims": len(base_ids),
        "new_claims": len(new_ids),
        "first_run": {k: first[k] for k in FIRST_RUN_KEYS},
        "second_run": {k: second[k] for k in SECOND_RUN_KEYS},
        "edits_survived": _survived(after["groups"], expected),
        "rewritten_claim_kept_group": group_of.get(rewritten) == rewritten_group["group_id"],
        "dropped_claim_gone": dropped not in group_of,
        "base_group_ids_kept": f"{len(kept & after_ids)}/{len(kept)}",
        "dropped_group_gone": dropped_group["group_id"] not in after_ids,
        "derived_fields_match_recount": recount_ok,
        "new_claim_pairs": {
            "incremental": len(incremental),
            "fresh_grouping": len(fresh_new),
            "in_both": len(incremental & fresh_new),
        },
        "third_run_written": third["written"],
    }
