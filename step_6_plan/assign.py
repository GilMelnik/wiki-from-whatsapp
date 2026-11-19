"""Step 6b: put every claim on its page(s) and labels (contract C8).

How it works, in plain words: after the owner has reviewed the page plan, the cheap
classifier (stage ``assign.stage``, Jev) reads each claim's text and entity names and
answers, in one request, which page it belongs on (from the page titles and scopes only) and
which labels apply. ``step_6_plan.assign_rules`` turns the probabilities into pages (one, two
when the classifier clearly splits between two, or none) and labels. Claims that fit no page
are not forced onto one: they are handed to the ``propose`` chat stage
(``step_6_plan.assign_proposals``), which proposes a missing page, label or wider scope. The
step never applies a proposal; only the owner does, in the plan tool.

Re-runs (C14, custom merge): the run starts from the current EDITED assignments.
``metadata.inputs`` holds a fingerprint per claim, keyed by short id (the text the classifier
reads), and ``metadata.plan`` one per page and label (what the questions say about it). New and
changed claims are asked in full; removed ones are dropped. A plan change asks only what it
affects: merged ids are followed, deleted ones cleared; claims on a re-scoped or deleted page,
claims that leaned towards a re-scoped page and all unplaced claims are asked the page question
again, and every other claim is screened with one cheap yes/no question per new or re-scoped
page; a new or re-worded label is asked of every claim on its own. Records the owner set by
hand (``manual: true``) are never re-decided, only followed through merges and cleared of
deleted ids (one that loses all its pages that way is asked again and reported). Proposals
keep the owner's status; a claim already named by a proposal, or already shown to the
proposer (``metadata.considered``, keyed by short id), is not proposed again. Nothing is written when nothing changed (unless an input
file is newer than the output, so the review tool's stale notice clears).

    python -m step_6_plan.assign [--rebuild] [--only SHORT_ID ...] [--estimate]

``--estimate`` asks nothing and writes nothing: it counts the claims the classifier would read
in full and gives an upper bound of the proposal calls (the step's only chat calls).

Output: ``assignments.json`` == ``assignments_edited.json`` (C8)::

    {"assignments": {short_id: {pages, labels, p, label_p, manual?}},   # C6 record order
     "proposals": [{id, kind, claims, title_he, description, reason, status, ...}],
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
from pathlib import Path
from typing import Any

from step_6_plan.assign_proposals import PROPOSALS_CONFIG, Proposer, proposed_claims
from step_6_plan.assign_rules import (
    ASSIGN_CONFIG,
    Record,
    claims_on_page,
    follow_all,
    item_id,
    kept,
    label_question,
    plan_changes,
    plan_snapshot,
    redecide,
    screen_question,
)
from step_6_plan.claim_records import ClaimSources, load_claim_records
from step_6_plan.claim_records import Record as Claim
from step_6_plan.coverage import claim_state, page_question
from step_6_plan.model import PlanError, validation_errors
from utils.json_io import read_json_if_present
from utils.llm_client import Classifier, LLMClient, Question
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import (
    ASSIGNMENTS,
    PLAN,
    STEP_6,
    Artifact,
    resolve,
    resolve_claims_path,
    resolve_entities_path,
)
from utils.reconcile import diff_inputs, fingerprint, guarded_write
from utils.timestamps import utc_now

COMPARED = ("assignments", "proposals")
COMPARED_METADATA = ("inputs", "plan", "considered")


def _chunks(items: Sequence[Question], size: int) -> list[Sequence[Question]]:
    return [items[start:start + size] for start in range(0, len(items), size)] or [()]


class Asker:
    """The classifier questions of one plan, asked for many claims at once."""

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

    def ask(self, claims: Mapping[str, Claim], page_ids: Collection[str],
            labels: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
        """Ask the page question of the claims in ``page_ids`` and the label questions in
        ``labels[short_id]``; returns ``{short_id: {"p"?: kept page probs, "label_p": {...}}}``
        (``label_p`` holds every asked label, below the floor as 0.0 so it can be removed)."""

        items: list[tuple[str, list[Question]]] = []
        owners: list[str] = []
        for short_id, claim in claims.items():
            wanted = [self.label_questions[lid] for lid in labels.get(short_id, ())]
            for index, chunk in enumerate(_chunks(wanted, ASSIGN_CONFIG["max_label_questions"])):
                questions = [*([self.page_question] if index == 0 and short_id in page_ids else []),
                             *chunk]
                if questions:
                    items.append((claim_state(claim), questions))
                    owners.append(short_id)
        results: dict[str, dict[str, Any]] = {}
        for short_id, (_, questions), answers in zip(owners, items, self._classify(items, "assign")):
            result = results.setdefault(short_id, {"label_p": {}})
            for question, answer in zip(questions, answers):
                if question is self.page_question:
                    result["p"] = kept(answer.probabilities, self.plan["pages"])
                else:
                    result["label_p"][item_id(question)] = answer.probabilities
        return results

    def screen(self, claims: Mapping[str, Claim], page_ids: Sequence[str]) -> set[str]:
        """The claims that might belong on one of ``page_ids`` (a yes/no question per page)."""

        questions = [screen_question(pid, self.plan["pages"][pid]) for pid in page_ids]
        chunks = _chunks(questions, ASSIGN_CONFIG["max_label_questions"])
        pairs = [(sid, list(chunk)) for sid in claims for chunk in chunks]
        answers = self._classify([(claim_state(claims[sid]), qs) for sid, qs in pairs],
                                 "assign-screen")
        floor = ASSIGN_CONFIG["screen_probability"]
        return {sid for (sid, _), answer in zip(pairs, answers)
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
        "claim_count": len(records),
        "unplaced": sum(1 for r in records if not r["pages"]),
        "two_pages": sum(1 for r in records if len(r["pages"]) > 1),
        "manual": sum(1 for r in records if r.get("manual")),
        "page_sizes": {pid: {"all": len(claims_on_page(assignments, pid)),
                             "primary": len(claims_on_page(assignments, pid, primary_only=True))}
                       for pid in plan["pages"]},
        "label_counts": {lid: sum(1 for r in records if lid in r["labels"]) for lid in plan["labels"]},
    }


class AssignmentRun:
    """One run of the step over the current claims, plan and stored assignments."""

    def __init__(self, claims: Sequence[Claim], plan: dict[str, Any],
                 state: Mapping[str, Any], base: Mapping[str, Any], *, rebuild: bool,
                 only: Collection[str] | None, logger: logging.Logger) -> None:
        self.claims = {claim["short_id"]: claim for claim in claims}
        self.plan = plan
        self.logger = logger
        self.rebuild = rebuild
        self.scope = None if only is None else set(only)
        meta = {} if rebuild else state.get("metadata") or {}
        stored = copy.deepcopy(state.get("assignments") or {})
        self.records: dict[str, Record] = (
            {sid: r for sid, r in stored.items() if r.get("manual")} if rebuild else stored
        )
        self.base = base.get("assignments") or {}
        self.proposals: list[dict[str, Any]] = copy.deepcopy(state.get("proposals") or [])
        self.old_inputs: dict[str, str] = meta.get("inputs") or {}
        self.snapshot = meta.get("plan")
        self.considered: dict[str, str] = dict(meta.get("considered") or {})
        self.inputs = {sid: fingerprint(claim_state(c)) for sid, c in self.claims.items()}
        self.stats: dict[str, Any] = {}

    def in_scope(self, short_id: str) -> bool:
        return self.scope is None or short_id in self.scope

    def automatic(self, ids: Iterable[str]) -> list[str]:
        """``ids`` that are current claims in scope without a manual record, in record order."""
        wanted = set(ids)
        return [sid for sid in self.claims if sid in wanted and self.in_scope(sid)
                and not self.records.get(sid, {}).get("manual")]

    def follow_plan(self) -> tuple[set[str], tuple[str, ...], tuple[str, ...]]:
        """Follow plan changes; returns (claims to ask the page again, pages to screen for,
        labels to ask again)."""

        changes = plan_changes(self.snapshot, self.plan)
        was_manual = {sid for sid, record in self.records.items() if record.get("manual")}
        edit = follow_all(self.records, changes, self.plan)
        self.records = edit.assignments
        manual_cleared = {sid: ids for sid, ids in edit.cleared.items() if sid in was_manual}
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
        """Claims asked every question: new or changed, without a record, un-pinned by the
        owner (manual in the last output, not any more), everything on rebuild or ``only``."""

        unpinned = {sid for sid, r in self.records.items()
                    if not r.get("manual") and self.base.get(sid, {}).get("manual")}
        missing = {sid for sid in self.claims if sid not in self.records}
        forced = set(self.claims) if self.rebuild or self.scope is not None else set()
        return set(self.automatic({*diff_ids, *unpinned, *missing, *forced}))

    def assign(self, classifier: Classifier) -> None:
        """Ask what has to be asked and re-decide every automatic record."""

        diff = diff_inputs(
            {sid: fp for sid, fp in self.old_inputs.items() if self.in_scope(sid)},
            {sid: fp for sid, fp in self.inputs.items() if self.in_scope(sid)},
        )
        for sid in diff.removed:
            self.records.pop(sid, None)
        reask, screen_pages, changed_labels = self.follow_plan()
        full = self.full_set((*diff.new, *diff.changed))
        page_only = set(self.automatic(reask)) - full
        asker = Asker(self.plan, classifier)
        rest = self.automatic(sid for sid in self.claims if sid not in full and sid not in page_only)
        if screen_pages and rest:
            page_only |= asker.screen({sid: self.claims[sid] for sid in rest}, screen_pages)
        label_ids = list(self.plan["labels"])
        labels = {sid: label_ids for sid in full}
        labels.update({sid: list(changed_labels) for sid in self.automatic(self.claims)
                       if sid not in full and changed_labels})
        asked = [sid for sid in self.claims if sid in full or sid in page_only or sid in labels]
        results = asker.ask({sid: self.claims[sid] for sid in asked}, full | page_only, labels)
        for sid, result in results.items():
            self.records[sid] = _update(self.records.get(sid), result, full=sid in full)
        self.records = {sid: redecide(self.records[sid], label_ids)
                        for sid in self.claims if sid in self.records}
        self.stats.update(full=len(full), page_only=len(page_only),
                          label_only=len(set(labels) - full - page_only), removed=len(diff.removed),
                          classifier_requests=asker.requests)

    def estimate(self) -> dict[str, Any]:
        """What ``assign`` + ``propose`` would do at most, without asking anything.

        A proposal candidate is an automatic claim, not named by a proposal and not shown to
        the proposer with its current text, that can be unplaced after the run: any such claim
        when the plan changed, else the claims asked in full and those unplaced now."""

        diff = diff_inputs(
            {sid: fp for sid, fp in self.old_inputs.items() if self.in_scope(sid)},
            {sid: fp for sid, fp in self.inputs.items() if self.in_scope(sid)},
        )
        full = self.full_set((*diff.new, *diff.changed))
        changes = plan_changes(self.snapshot, self.plan)
        plan_changed = self.snapshot is not None and any((
            changes.created_pages, changes.rescoped_pages, changes.removed_pages,
            changes.changed_labels, changes.removed_labels))
        unplaced = {sid for sid, record in self.records.items() if not record["pages"]}
        reach = self.automatic(self.claims if plan_changed else full | unplaced)
        named = proposed_claims(self.proposals)
        candidates = [sid for sid in reach
                      if sid not in named and self.considered.get(sid) != self.inputs[sid]]
        calls = (0 if len(candidates) < PROPOSALS_CONFIG["min_claims"]
                 else math.ceil(len(candidates) / PROPOSALS_CONFIG["max_claims_per_call"]))
        return {"estimate": True, "claims_asked": len(full), "plan_changed": plan_changed,
                "proposal_candidates_max": len(candidates), "proposal_calls_max": calls,
                "written": False}

    def propose(self, llm: LLMClient | None) -> None:
        """Ask for proposals for unplaced claims not yet named or shown (at least
        ``proposals.min_claims`` of them)."""

        named = proposed_claims(self.proposals)
        candidates = [sid for sid in self.automatic(self.records)
                      if not self.records[sid]["pages"] and sid not in named
                      and self.considered.get(sid) != self.inputs[sid]]
        self.stats["proposal_candidates"] = len(candidates)
        if len(candidates) < PROPOSALS_CONFIG["min_claims"]:
            self.stats["proposal_calls"] = 0
            return
        llm = llm or LLMClient.for_stage(PROPOSALS_CONFIG["stage"], logger=self.logger,
                                         max_tokens=PROPOSALS_CONFIG["max_output_tokens"])
        proposer = Proposer(llm, self.logger)
        made = proposer.propose(self.plan, [self.claims[sid] for sid in candidates], self.proposals)
        self.proposals += made
        self.considered.update({sid: self.inputs[sid] for sid in candidates})
        self.stats.update(proposal_calls=proposer.calls, new_proposals=[p["title_he"] for p in made])

    def payload(self, source: Path, plan_source: Path, classifier: Classifier) -> dict[str, Any]:
        inputs = {sid: fp for sid, fp in self.old_inputs.items()
                  if sid in self.claims and not self.in_scope(sid)}
        inputs.update({sid: fp for sid, fp in self.inputs.items() if self.in_scope(sid)})
        inputs = {sid: inputs[sid] for sid in self.claims if sid in inputs}
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
                "considered": {sid: fp for sid, fp in self.considered.items() if sid in self.claims},
                "params": ASSIGN_CONFIG,
                "classifier": f"{classifier.provider}/{classifier.model}",
                "git_commit": git_commit(),
                "generated_at": utc_now(),
                "run": self.stats,
                "summary": summary(self.records, self.plan),
            },
        }


def _claim_files(sources: ClaimSources) -> list[Path]:
    """The files the claim records are joined from that exist (the manual contradictions are
    optional)."""

    files = (sources.claims or resolve_claims_path(), sources.entities or resolve_entities_path(),
             sources.audit, sources.contradictions)
    return [path for path in files if path.is_file()]


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
    sources: ClaimSources = ClaimSources(),
    plan_path: Path | None = None,
    artifact: Artifact = ASSIGNMENTS,
    only: Collection[str] | None = None,
    rebuild: bool = False,
    classifier: Classifier | None = None,
    llm: LLMClient | None = None,
    estimate: bool = False,
) -> dict[str, Any]:
    """Assign the claims to the plan's pages and labels; see the module docstring.

    ``sources``: the files the claim records are joined from (default: the effective claims
    and entities, the audit and the manual contradictions); ``plan_path`` defaults to the
    effective (edited) plan. ``only``: (re)assign exactly these claims (short ids; asked in
    full, cached answers are free) and leave every other record and the stored plan snapshot
    as they are. ``rebuild``: ask every automatic claim again (manual records and proposals
    stay). ``classifier`` / ``llm``
    default to the configured stages (the chat client is built only when proposals are due).
    ``estimate``: ask nothing, write nothing; return ``AssignmentRun.estimate``.
    Returns the run counters (``full``, ``page_only``, ``label_only``, ``classifier_requests``,
    ``proposal_calls``, ...), ``summary``, ``written`` and ``backup``.
    """

    logger = setup_step_logging(STEP_6)
    source = sources.claims or resolve_claims_path()
    plan_source = plan_path or resolve(PLAN)
    claims = load_claim_records(sources)
    plan = json.loads(plan_source.read_text(encoding="utf-8"))
    errors = validation_errors(plan)
    if errors:
        raise PlanError([f"{plan_source} is not a valid C7 plan; fix it in the plan tool", *errors])
    base = read_json_if_present(artifact.original) or {}
    before = read_json_if_present(artifact.edited) or base
    job = AssignmentRun(claims, plan, before, base, rebuild=rebuild, only=only, logger=logger)
    if estimate:
        result = job.estimate()
        logger.info(f"Assignment estimate: {result}")
        return result
    classifier = classifier or Classifier.for_stage(ASSIGN_CONFIG["stage"], logger=logger)
    job.assign(classifier)
    job.propose(llm)
    payload = job.payload(source, plan_source, classifier)
    result = {**job.stats, "summary": payload["metadata"]["summary"], "backup": None}
    if not rebuild and _unchanged(payload, before, artifact.original,
                                  (*_claim_files(sources), plan_source)):
        logger.info(f"Assignments unchanged ({len(job.records)} claims); nothing written")
        return {**result, "written": False}
    backup = guarded_write(artifact.edited, payload, backup=True)
    guarded_write(artifact.original, payload, backup=False)
    stats = payload["metadata"]["summary"]
    logger.info(
        f"Assignments written: {stats['claim_count']} claims, {stats['unplaced']} unplaced, "
        f"{stats['two_pages']} on two pages, {stats['manual']} manual; run {job.stats}; "
        f"backup {backup}"
    )
    return {**result, "written": True, "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 6b: assign claims to pages (C8).")
    parser.add_argument("--rebuild", action="store_true",
                        help="ask every automatic claim again (manual records and proposals stay)")
    parser.add_argument("--only", nargs="+", metavar="SHORT_ID",
                        help="(re)assign only these claims (their short ids, g-xxxxxxxx)")
    parser.add_argument("--estimate", action="store_true",
                        help="count what a run would ask; ask nothing, write nothing")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, only=args.only, estimate=args.estimate)


if __name__ == "__main__":
    main()
