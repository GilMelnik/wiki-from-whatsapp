"""Step 5: group the claims that say the same thing, across the whole chat (contract C6).

How it works, in plain words: every claim is turned into a vector by the hosted embedder, so
that claims worded differently but meaning the same land close together. Claims are then
taken oldest first; each one joins the existing group whose *every* member it closely
matches, or starts a new group of its own. "Closely matches" (see ``similarity``) means: not
an opposite stance (a recommendation never joins a warning), no clash of quoted numbers, and a
cosine similarity of at least ``merge_threshold.shared_entity`` when both claims mention a
common canonical entity from step 4, or the higher ``merge_threshold.no_shared_entity`` when
they do not. Requiring a match with every member keeps groups tight: a chain of small
differences can never pull two different statements together. Groups holding
opposite-stance claims about a common entity with very similar wording are linked as
``contradicts``. For every group the step then counts the distinct people who backed or
disputed any of its claims, per month, from the private audit (see ``derive``). There is no
LLM call and no randomness.

Re-runs (C14, custom merge): ``metadata.inputs`` holds a fingerprint per claim (text, stance,
canonical entity ids). A run starts from the CURRENT EDITED groups, takes removed claims out,
moves a changed claim out only when its stance now opposes a fellow member, places new
claims exactly as above (a re-run and a fresh run follow the same rule), applies new or
changed manual must-links (``MANUAL_AGGREGATIONS_PATH``), drops empty groups and recomputes
every derived field of every group. Membership, ids, ``merged_from``, a manual
representative (``representative_manual``) and manual contradiction links
(``contradicts_manual`` / ``contradicts_rejected``) are never re-derived, so a reviewer's
merge, split, move, representative or link choice survives; top-level keys of the edited file
other than ``groups`` / ``metadata`` (the reviewer's ``not_same`` list) are carried over. Nothing is written when nothing changed.
``--rebuild`` regroups from scratch after backing up the edited file.

Output (``claims_aggregated.json`` == ``claims_aggregated_edited.json`` after a run)::

    {"groups": [{group_id, claim_text, representative_claim_id, stance, member_claim_ids,
       entities, entity_names, supporter_count, opposer_count, first_date, last_date,
       by_month: {"YYYY-MM": {supporters, opposers}}, thread_count, contradicts,
       merged_from?, representative_manual?, contradicts_manual?, contradicts_rejected?}],
     "not_same"?: [{id, claims_a, claims_b, group_ids, source, at}],   (groups reviewer)
     "metadata": {source, inputs, must_links, params, embedding_model, git_commit, run,
       group_count, multi_member_group_count, claim_count, groups_with_contradictions}}
"""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from step_5_aggregate.derive import GroupDeriver, link_contradictions
from step_5_aggregate.groups import GroupBook
from step_5_aggregate.inputs import MustLink, StepInputs, load_inputs, load_must_links
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer
from utils.config import section
from utils.embedding_ab import _git_commit
from utils.logging_setup import setup_step_logging
from utils.paths import AGGREGATED, AUDIT_PATH, MANUAL_AGGREGATIONS_PATH, STEP_5, Artifact
from utils.reconcile import InputDiff, diff_inputs, guarded_write, load_previous

PARAM_KEYS = ("merge_threshold", "contradiction_threshold", "opposing_stances", "embedding_kind")


def assign_new_claims(
    state: dict[str, Any],
    inputs: StepInputs,
    scorer: ClaimScorer,
    diff: InputDiff,
    must_links: Sequence[MustLink] = (),
) -> dict[str, int]:
    """The incremental core: fold ``diff`` into the groups of ``state`` (edited in place).

    ``state`` is a C6 payload (``groups`` + ``metadata.must_links``, the fingerprints of the
    must-links applied so far). Removed claims leave their groups; a changed claim stays
    unless its stance now opposes a fellow member; new claims (and changed claims in no
    group) are placed into the current groups or open new ones; new / changed must-links are
    applied; empty groups are dropped; every derived field is recomputed. Returns counters.
    """

    book = GroupBook(state.setdefault("groups", []), inputs.claims, scorer)
    left = book.remove(diff.removed)
    left += book.remove([c for c in list(book.group_of) if c not in inputs.claims])
    left += len(book.eject_opposed(diff.changed))
    pending = [c for c in (*diff.new, *diff.changed) if c not in book.group_of]
    placed = book.place(pending, must_links)
    metadata = state.setdefault("metadata", {})
    metadata["must_links"], linked = book.apply_must_links(
        must_links, metadata.get("must_links") or {}
    )
    dropped = book.drop_empty()
    deriver = GroupDeriver(inputs, scorer)
    state["groups"] = [deriver.refresh(group) for group in book.groups]
    return {
        "new_claims": len(diff.new),
        "changed_claims": len(diff.changed),
        "removed_claims": len(diff.removed),
        "claims_left_groups": left,
        **placed,
        "must_links_applied": linked,
        "groups_dropped": dropped,
        "contradiction_links": link_contradictions(state["groups"], scorer),
    }


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _counts(groups: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "group_count": len(groups),
        "multi_member_group_count": sum(1 for g in groups if len(g["member_claim_ids"]) > 1),
        "claim_count": sum(len(g["member_claim_ids"]) for g in groups),
        "groups_with_contradictions": sum(1 for g in groups if g["contradicts"]),
    }


def run(
    *,
    claims_path: Path | None = None,
    entities_path: Path | None = None,
    audit_path: Path = AUDIT_PATH,
    must_links_path: Path = MANUAL_AGGREGATIONS_PATH,
    artifact: Artifact = AGGREGATED,
    only: Collection[str] | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    """Create or incrementally update the claim groups; see the module docstring.

    Claims and entities default to their reviewed (edited) files. ``only`` restricts the
    new / changed / removed claims to these ids (other claims keep their place and their
    fingerprints); ``rebuild`` regroups everything. Returns the counters of
    ``assign_new_claims`` plus ``group_count``, ``multi_member_group_count``,
    ``claim_count``, ``groups_with_contradictions``, ``written`` and ``backup``.
    """

    logger = setup_step_logging(STEP_5)
    inputs = load_inputs(claims_path, entities_path, audit_path)
    previous = {} if rebuild else load_previous(artifact)
    state = {"groups": []} if rebuild else copy.deepcopy(_read(artifact.edited) or previous)
    before = copy.deepcopy(state)
    scope = None if only is None else set(only)
    current = {
        cid: inputs.fingerprint(cid) for cid in inputs.claims if scope is None or cid in scope
    }
    prev_inputs = previous.get("metadata", {}).get("inputs") or {}
    diff = diff_inputs(
        {cid: fp for cid, fp in prev_inputs.items() if scope is None or cid in scope}, current
    )
    scorer = ClaimScorer.embed(inputs.claims, inputs.claim_entities)
    stats = assign_new_claims(state, inputs, scorer, diff, load_must_links(inputs, must_links_path))
    out_inputs = {
        cid: fp for cid, fp in prev_inputs.items() if scope is not None and cid not in scope
    }
    out_inputs.update(current)
    old_meta = before.get("metadata") or {}
    counts = _counts(state["groups"])
    if (
        not rebuild
        and state["groups"] == before.get("groups")
        and out_inputs == old_meta.get("inputs")
        and state["metadata"]["must_links"] == (old_meta.get("must_links") or {})
    ):
        logger.info(f"Groups: {counts['group_count']} groups unchanged; nothing written")
        return {**stats, **counts, "written": False, "backup": None}

    embedding = section("embedding")
    state["metadata"] = {
        "source": str(inputs.source),
        "inputs": out_inputs,
        "must_links": state["metadata"]["must_links"],
        "params": {key: AGGREGATE_CONFIG[key] for key in PARAM_KEYS},
        "embedding_model": f"{embedding['model']}-{embedding['output_dimensionality']}",
        "git_commit": _git_commit(),
        "run": stats,
        **counts,
    }
    backup = guarded_write(artifact.edited, state, backup=True)
    guarded_write(artifact.original, state, backup=False)
    logger.info(
        f"Groups: {counts['group_count']} groups ({counts['multi_member_group_count']} with "
        f"several claims) from {counts['claim_count']} claims; run {stats}; "
        f"commit {state['metadata']['git_commit']}; params {state['metadata']['params']}"
    )
    return {**stats, **counts, "written": True, "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 5: group claims (incremental).")
    parser.add_argument("--rebuild", action="store_true", help="regroup from scratch")
    parser.add_argument("--only", nargs="+", metavar="CLAIM_ID", help="restrict to these claims")
    args = parser.parse_args(argv)
    run(only=args.only, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
