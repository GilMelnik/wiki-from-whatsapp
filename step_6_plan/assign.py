"""Step 6b: put every claim group on its page(s) and labels (contract C8).

How it works, in plain words: after the owner has reviewed the page plan, the cheap
classifier (stage ``assign.stage``, Jev) reads each claim group's text and entity names and
answers, in one request, which page it belongs on (from the page titles and scopes only) and
which labels apply. ``step_6_plan.assign_rules`` turns the probabilities into pages (one, two
when the classifier clearly splits between two, or none) and labels. Groups that fit no page
are not forced onto one: they are handed to the ``propose`` chat stage
(``step_6_plan.assign_proposals``), which proposes a missing page, label or wider scope. The
step never applies a proposal; only the owner does, in the plan tool.

Re-runs (C14, custom merge): the run starts from the current EDITED assignments.
``metadata.inputs`` holds a fingerprint per group (the text the classifier reads) and
``metadata.plan`` one per page and label (what the questions say about it). New and changed
groups are asked in full; removed ones are dropped. A plan change asks only what it affects:
merged ids are followed, deleted ones cleared; groups on a re-scoped or deleted page, groups
that leaned towards a re-scoped page and all unplaced groups are asked the page question
again, and every other group is screened with one cheap yes/no question per new or re-scoped
page; a new or re-worded label is asked of every group on its own. Records the owner set by
hand (``manual: true``) are never re-decided, only followed through merges and cleared of
deleted ids (one that loses all its pages that way is asked again and reported). Proposals
keep the owner's status; a group already named by a proposal, or already shown to the
proposer, is not proposed again. Nothing is written when nothing changed (unless an input
file is newer than the output, so the review tool's stale notice clears).

    python -m step_6_plan.assign [--rebuild] [--only GROUP_ID ...] [--estimate]

``--estimate`` asks nothing and writes nothing: it counts the groups the classifier would read
in full and gives an upper bound of the proposal calls (the step's only chat calls).

Output: ``assignments.json`` == ``assignments_edited.json`` (C8)::

    {"assignments": {group_id: {pages, labels, p, label_p, manual?}},   # C6 group order
     "proposals": [{id, kind, group_ids, title_he, description, reason, status, ...}],
     "metadata": {source, plan_source, inputs, plan, considered, params, classifier,
                  git_commit, generated_at, run, summary}}
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from step_6_plan.assign_proposals import PROPOSALS_CONFIG, Proposer, proposed_group_ids
from step_6_plan.assign_rules import (
    ASSIGN_CONFIG,
    Record,
    follow_all,
    groups_on_page,
    item_id,
    kept,
    label_question,
    plan_changes,
    plan_snapshot,
    redecide,
    screen_question,
)
from step_6_plan.coverage import group_state, page_question
from step_6_plan.model import PlanError, validation_errors
from utils.embedding_ab import _git_commit
from utils.llm_client import Classifier, LLMClient, Question
from utils.logging_setup import setup_step_logging
from utils.paths import ASSIGNMENTS, PLAN, STEP_6, Artifact, resolve, resolve_aggregated_path
from utils.reconcile import diff_inputs, fingerprint, guarded_write

COMPARED = ("assignments", "proposals")
COMPARED_METADATA = ("inputs", "plan", "considered")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def _chunks(items: Sequence[Question], size: int) -> list[Sequence[Question]]:
    return [items[start:start + size] for start in range(0, len(items), size)] or [()]


class Asker:
    """The classifier questions of one plan, asked for many groups at once."""

    def __init__(self, plan: Mapping[str, Any], classifier: Classifier) -> None:
        self.plan = plan
        self.classifier = classifier
        self.page_question = page_question(dict(plan))
        self.label_questions = {lid: label_question(lid, label)
                                for lid, label in plan["labels"].items()}
        self.requests = 0

    def _classify(self, items: list[tuple[str, list[Question]]], task: str) -> list[list[Any]]:
        self.requests += len(items)
        return self.classifier.classify_many(items, task=task) if items else []

    def ask(self, groups: Mapping[str, dict[str, Any]], page_ids: Collection[str],
            labels: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
        """Ask the page question of the groups in ``page_ids`` and the label questions in
        ``labels[group_id]``; returns ``{group_id: {"p"?: kept page probs, "label_p": {...}}}``
        (``label_p`` holds every asked label, below the floor as 0.0 so it can be removed)."""

        items: list[tuple[str, list[Question]]] = []
        owners: list[str] = []
        for group_id, group in groups.items():
            wanted = [self.label_questions[lid] for lid in labels.get(group_id, ())]
            for index, chunk in enumerate(_chunks(wanted, ASSIGN_CONFIG["max_label_questions"])):
                questions = [*([self.page_question] if index == 0 and group_id in page_ids else []),
                             *chunk]
                if questions:
                    items.append((group_state(group), questions))
                    owners.append(group_id)
        results: dict[str, dict[str, Any]] = {}
        for group_id, (_, questions), answers in zip(owners, items, self._classify(items, "assign")):
            result = results.setdefault(group_id, {"label_p": {}})
            for question, answer in zip(questions, answers):
                if question is self.page_question:
                    result["p"] = kept(answer.probabilities, self.plan["pages"])
                else:
                    result["label_p"][item_id(question)] = answer.probabilities
        return results

    def screen(self, groups: Mapping[str, dict[str, Any]], page_ids: Sequence[str]) -> set[str]:
        """The groups that might belong on one of ``page_ids`` (a yes/no question per page)."""

        questions = [screen_question(pid, self.plan["pages"][pid]) for pid in page_ids]
        chunks = _chunks(questions, ASSIGN_CONFIG["max_label_questions"])
        pairs = [(gid, list(chunk)) for gid in groups for chunk in chunks]
        answers = self._classify([(group_state(groups[gid]), qs) for gid, qs in pairs],
                                 "assign-screen")
        floor = ASSIGN_CONFIG["screen_probability"]
        return {gid for (gid, _), answer in zip(pairs, answers)
                if any(a.probabilities >= floor for a in answer)}


def _update(record: Record | None, result: Mapping[str, Any], *, full: bool) -> Record:
    """A record with fresh answers: ``full`` replaces every probability, otherwise only the
    page probabilities (if asked) and the labels that were asked."""

    record = copy.deepcopy(record) if record else {"pages": [], "labels": [], "p": {}, "label_p": {}}
    if "p" in result:
        record["p"] = result["p"]
    asked = result["label_p"]
    floor = ASSIGN_CONFIG["kept_probability"]
    base = {} if full else {k: v for k, v in record["label_p"].items() if k not in asked}
    record["label_p"] = {**base, **{k: v for k, v in asked.items() if v >= floor}}
    return record


def summary(assignments: Mapping[str, Record], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Page sizes (all / as primary), unplaced, two-page, manual and label counts."""

    records = list(assignments.values())
    return {
        "group_count": len(records),
        "unplaced": sum(1 for r in records if not r["pages"]),
        "two_pages": sum(1 for r in records if len(r["pages"]) > 1),
        "manual": sum(1 for r in records if r.get("manual")),
        "page_sizes": {pid: {"all": len(groups_on_page(assignments, pid)),
                             "primary": len(groups_on_page(assignments, pid, primary_only=True))}
                       for pid in plan["pages"]},
        "label_counts": {lid: sum(1 for r in records if lid in r["labels"]) for lid in plan["labels"]},
    }


class AssignmentRun:
    """One run of the step over the current groups, plan and stored assignments."""

    def __init__(self, groups: Sequence[dict[str, Any]], plan: dict[str, Any],
                 state: Mapping[str, Any], base: Mapping[str, Any], *, rebuild: bool,
                 only: Collection[str] | None, logger: logging.Logger) -> None:
        self.groups = {group["group_id"]: group for group in groups}
        self.plan = plan
        self.logger = logger
        self.rebuild = rebuild
        self.scope = None if only is None else set(only)
        meta = {} if rebuild else state.get("metadata") or {}
        stored = copy.deepcopy(state.get("assignments") or {})
        self.records: dict[str, Record] = (
            {gid: r for gid, r in stored.items() if r.get("manual")} if rebuild else stored
        )
        self.base = base.get("assignments") or {}
        self.proposals: list[dict[str, Any]] = copy.deepcopy(state.get("proposals") or [])
        self.old_inputs: dict[str, str] = meta.get("inputs") or {}
        self.snapshot = meta.get("plan")
        self.considered: dict[str, str] = dict(meta.get("considered") or {})
        self.inputs = {gid: fingerprint(group_state(g)) for gid, g in self.groups.items()}
        self.stats: dict[str, Any] = {}

    def in_scope(self, group_id: str) -> bool:
        return self.scope is None or group_id in self.scope

    def automatic(self, ids: Iterable[str]) -> list[str]:
        """``ids`` that are current groups in scope without a manual record, in group order."""
        wanted = set(ids)
        return [gid for gid in self.groups if gid in wanted and self.in_scope(gid)
                and not self.records.get(gid, {}).get("manual")]

    def follow_plan(self) -> tuple[set[str], tuple[str, ...], tuple[str, ...]]:
        """Follow plan changes; returns (groups to ask the page again, pages to screen for,
        labels to ask again)."""

        changes = plan_changes(self.snapshot, self.plan)
        was_manual = {gid for gid, record in self.records.items() if record.get("manual")}
        edit = follow_all(self.records, changes, self.plan)
        self.records = edit.assignments
        manual_cleared = {gid: ids for gid, ids in edit.cleared.items() if gid in was_manual}
        self.stats["cleared_manual"] = manual_cleared
        if manual_cleared:
            self.logger.warning(f"Deleted plan ids taken off manual assignments: {manual_cleared}")
        if self.snapshot is None:
            return set(), (), ()
        self.stats["plan_changes"] = {
            "created_pages": list(changes.created_pages), "rescoped_pages": list(changes.rescoped_pages),
            "removed_pages": list(changes.removed_pages), "changed_labels": list(changes.changed_labels),
            "removed_labels": list(changes.removed_labels),
        }
        return set(edit.reask), changes.screen_pages, changes.changed_labels

    def full_set(self, diff_ids: Iterable[str]) -> set[str]:
        """Groups asked every question: new or changed, without a record, un-pinned by the
        owner (manual in the last output, not any more), everything on rebuild or ``only``."""

        unpinned = {gid for gid, r in self.records.items()
                    if not r.get("manual") and self.base.get(gid, {}).get("manual")}
        missing = {gid for gid in self.groups if gid not in self.records}
        forced = set(self.groups) if self.rebuild or self.scope is not None else set()
        return set(self.automatic({*diff_ids, *unpinned, *missing, *forced}))

    def assign(self, classifier: Classifier) -> None:
        """Ask what has to be asked and re-decide every automatic record."""

        diff = diff_inputs(
            {gid: fp for gid, fp in self.old_inputs.items() if self.in_scope(gid)},
            {gid: fp for gid, fp in self.inputs.items() if self.in_scope(gid)},
        )
        for gid in diff.removed:
            self.records.pop(gid, None)
        reask, screen_pages, changed_labels = self.follow_plan()
        full = self.full_set((*diff.new, *diff.changed))
        page_only = set(self.automatic(reask)) - full
        asker = Asker(self.plan, classifier)
        rest = self.automatic(gid for gid in self.groups if gid not in full and gid not in page_only)
        if screen_pages and rest:
            page_only |= asker.screen({gid: self.groups[gid] for gid in rest}, screen_pages)
        label_ids = list(self.plan["labels"])
        labels = {gid: label_ids for gid in full}
        labels.update({gid: list(changed_labels) for gid in self.automatic(self.groups)
                       if gid not in full and changed_labels})
        asked = [gid for gid in self.groups if gid in full or gid in page_only or gid in labels]
        results = asker.ask({gid: self.groups[gid] for gid in asked}, full | page_only, labels)
        for gid, result in results.items():
            self.records[gid] = _update(self.records.get(gid), result, full=gid in full)
        self.records = {gid: redecide(self.records[gid], label_ids)
                        for gid in self.groups if gid in self.records}
        self.stats.update(full=len(full), page_only=len(page_only),
                          label_only=len(set(labels) - full - page_only), removed=len(diff.removed),
                          classifier_requests=asker.requests)

    def estimate(self) -> dict[str, Any]:
        """What ``assign`` + ``propose`` would do at most, without asking anything.

        A proposal candidate is an automatic group, not named by a proposal and not shown to
        the proposer with its current text, that can be unplaced after the run: any such group
        when the plan changed, else the groups asked in full and those unplaced now."""

        diff = diff_inputs(
            {gid: fp for gid, fp in self.old_inputs.items() if self.in_scope(gid)},
            {gid: fp for gid, fp in self.inputs.items() if self.in_scope(gid)},
        )
        full = self.full_set((*diff.new, *diff.changed))
        changes = plan_changes(self.snapshot, self.plan)
        plan_changed = self.snapshot is not None and any((
            changes.created_pages, changes.rescoped_pages, changes.removed_pages,
            changes.changed_labels, changes.removed_labels))
        unplaced = {gid for gid, record in self.records.items() if not record["pages"]}
        reach = self.automatic(self.groups if plan_changed else full | unplaced)
        named = proposed_group_ids(self.proposals)
        candidates = [gid for gid in reach
                      if gid not in named and self.considered.get(gid) != self.inputs[gid]]
        calls = (0 if len(candidates) < PROPOSALS_CONFIG["min_groups"]
                 else math.ceil(len(candidates) / PROPOSALS_CONFIG["max_groups_per_call"]))
        return {"estimate": True, "groups_asked": len(full), "plan_changed": plan_changed,
                "proposal_candidates_max": len(candidates), "proposal_calls_max": calls,
                "written": False}

    def propose(self, llm: LLMClient | None) -> None:
        """Ask for proposals for unplaced groups not yet named or shown (at least
        ``proposals.min_groups`` of them)."""

        named = proposed_group_ids(self.proposals)
        candidates = [gid for gid in self.automatic(self.records)
                      if not self.records[gid]["pages"] and gid not in named
                      and self.considered.get(gid) != self.inputs[gid]]
        self.stats["proposal_candidates"] = len(candidates)
        if len(candidates) < PROPOSALS_CONFIG["min_groups"]:
            self.stats["proposal_calls"] = 0
            return
        llm = llm or LLMClient.for_stage(PROPOSALS_CONFIG["stage"], logger=self.logger,
                                         max_tokens=PROPOSALS_CONFIG["max_output_tokens"])
        proposer = Proposer(llm, self.logger)
        made = proposer.propose(self.plan, [self.groups[gid] for gid in candidates], self.proposals)
        self.proposals += made
        self.considered.update({gid: self.inputs[gid] for gid in candidates})
        self.stats.update(proposal_calls=proposer.calls, new_proposals=[p["title_he"] for p in made])

    def payload(self, source: Path, plan_source: Path, classifier: Classifier) -> dict[str, Any]:
        inputs = {gid: fp for gid, fp in self.old_inputs.items()
                  if gid in self.groups and not self.in_scope(gid)}
        inputs.update({gid: fp for gid, fp in self.inputs.items() if self.in_scope(gid)})
        inputs = {gid: inputs[gid] for gid in self.groups if gid in inputs}
        snapshot = (plan_snapshot(self.plan) if self.scope is None or self.snapshot is None
                    else self.snapshot)
        return {
            "assignments": self.records,
            "proposals": self.proposals,
            "metadata": {
                "source": str(source),
                "plan_source": str(plan_source),
                "inputs": inputs,
                "plan": snapshot,
                "considered": {gid: fp for gid, fp in self.considered.items() if gid in self.groups},
                "params": ASSIGN_CONFIG,
                "classifier": f"{classifier.provider}/{classifier.model}",
                "git_commit": _git_commit(),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "run": self.stats,
                "summary": summary(self.records, self.plan),
            },
        }


def _unchanged(payload: Mapping[str, Any], before: Mapping[str, Any], original: Path,
               inputs: Sequence[Path]) -> bool:
    """Nothing to write: same content as before, and the output is not older than its inputs
    (the review tool's stale notice is mtime-based, so an edit that changes no assignment
    still gets a fresh file)."""

    old_meta = before.get("metadata") or {}
    newest_input = max(path.stat().st_mtime_ns for path in inputs)
    return (original.is_file() and original.stat().st_mtime_ns >= newest_input
            and all(payload[key] == before.get(key) for key in COMPARED)
            and all(payload["metadata"][key] == old_meta.get(key) for key in COMPARED_METADATA))


def run(
    *,
    aggregated_path: Path | None = None,
    plan_path: Path | None = None,
    artifact: Artifact = ASSIGNMENTS,
    only: Collection[str] | None = None,
    rebuild: bool = False,
    classifier: Classifier | None = None,
    llm: LLMClient | None = None,
    estimate: bool = False,
) -> dict[str, Any]:
    """Assign the claim groups to the plan's pages and labels; see the module docstring.

    ``aggregated_path`` / ``plan_path`` default to the effective (edited) step-5 groups and
    plan. ``only``: (re)assign exactly these groups (asked in full; cached answers are free)
    and leave every other record and the stored plan snapshot as they are. ``rebuild``: ask
    every automatic group again (manual records and proposals stay). ``classifier`` / ``llm``
    default to the configured stages (the chat client is built only when proposals are due).
    ``estimate``: ask nothing, write nothing; return ``AssignmentRun.estimate``.
    Returns the run counters (``full``, ``page_only``, ``label_only``, ``classifier_requests``,
    ``proposal_calls``, ...), ``summary``, ``written`` and ``backup``.
    """

    logger = setup_step_logging(STEP_6)
    source = aggregated_path or resolve_aggregated_path()
    plan_source = plan_path or resolve(PLAN)
    groups = json.loads(source.read_text(encoding="utf-8"))["groups"]
    plan = json.loads(plan_source.read_text(encoding="utf-8"))
    errors = validation_errors(plan)
    if errors:
        raise PlanError([f"{plan_source} is not a valid C7 plan; fix it in the plan tool", *errors])
    base = _read(artifact.original) or {}
    before = _read(artifact.edited) or base
    job = AssignmentRun(groups, plan, before, base, rebuild=rebuild, only=only, logger=logger)
    if estimate:
        result = job.estimate()
        logger.info(f"Assignment estimate: {result}")
        return result
    classifier = classifier or Classifier.for_stage(ASSIGN_CONFIG["stage"], logger=logger)
    job.assign(classifier)
    job.propose(llm)
    payload = job.payload(source, plan_source, classifier)
    result = {**job.stats, "summary": payload["metadata"]["summary"], "backup": None}
    if not rebuild and _unchanged(payload, before, artifact.original, (source, plan_source)):
        logger.info(f"Assignments unchanged ({len(job.records)} groups); nothing written")
        return {**result, "written": False}
    backup = guarded_write(artifact.edited, payload, backup=True)
    guarded_write(artifact.original, payload, backup=False)
    stats = payload["metadata"]["summary"]
    logger.info(
        f"Assignments written: {stats['group_count']} groups, {stats['unplaced']} unplaced, "
        f"{stats['two_pages']} on two pages, {stats['manual']} manual; run {job.stats}; "
        f"backup {backup}"
    )
    return {**result, "written": True, "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 6b: assign claim groups to pages (C8).")
    parser.add_argument("--rebuild", action="store_true",
                        help="ask every automatic group again (manual records and proposals stay)")
    parser.add_argument("--only", nargs="+", metavar="GROUP_ID",
                        help="(re)assign only these groups")
    parser.add_argument("--estimate", action="store_true",
                        help="count what a run would ask; ask nothing, write nothing")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, only=args.only, estimate=args.estimate)


if __name__ == "__main__":
    main()
