"""Step 3: extract claims from knowledge-bearing threads, incrementally (contracts C3, C14).

Per thread (fingerprint = message ids + contents + reactions, ``metadata.inputs``):

- new thread -> the model reads the whole thread and returns claims;
- unchanged thread -> no call; its claims are carried;
- only reactions changed, or messages left the thread -> no call; counts, dates and evidence
  ids are recomputed;
- messages were added -> one call with the new messages marked and the thread's existing
  claims listed: existing claims keep id and text and may gain evidence, new claims are
  appended (a thread that had no claims is simply extracted again);
- thread gone or no longer knowledge-bearing -> its claims are dropped.

Disagreements: a dissent of substance is a claim of its own, linked to the claim it disputes
through ``counters`` (``step_3_extract.claims``); a new claim of an update may dispute an
existing one, which then gains the link too. For the threads the model read, the run counts
the links, the invalid references it dropped and where the opposing evidence and the links
disagree (``check_counters``, logged, never fatal).

A failed call leaves the thread's previous fingerprint (or none) in place, so the next run
retries it. Output: ``claims.json`` (public, C3) written through ``utils.reconcile`` with the
derived fields as ``refresh_keys`` -- a manual text edit, PII decision or deletion in
``claims_edited.json`` survives count / date changes -- plus the PRIVATE
``audit/claims_audit.json`` with every supporter / opposer identity per claim.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from collections.abc import Collection
from pathlib import Path
from typing import Any

from step_3_extract.claims import (
    DERIVED_KEYS,
    OutputError,
    ThreadClaims,
    check_counters,
    input_fingerprint,
    message_fingerprints,
)
from step_3_extract.prompt import EXTRACT_SYSTEM, FRESH_SCHEMA, UPDATE_SCHEMA
from step_3_extract.scrub import scrub_claims
from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import (
    AUDIT_PATH,
    CHAT_ANDROID_PATH,
    CLAIMS,
    STEP_3,
    Artifact,
    resolve_classified_path,
    resolve_threads_path,
)
from utils.reconcile import diff_inputs, load_previous, write_reconciled
from utils.threads_io import load_export_index, load_threads, with_export

AUDIT_WARNING = "PRIVATE - links claims to the people behind them. Do not publish."


def load_audit(path: Path) -> dict[str, dict[str, Any]]:
    """claim_id -> private audit record; ``{}`` when the file does not exist."""

    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return {record["claim_id"]: record for record in json.load(f).get("audit", [])}


def write_audit(path: Path, records: list[dict[str, Any]]) -> None:
    write_json_file(
        {"audit": records, "metadata": {"warning": AUDIT_WARNING, "claims_count": len(records)}},
        path,
    )


def knowledge_thread_ids(classified: dict[str, Any]) -> list[str]:
    """Knowledge-bearing thread ids; reads only ``thread_id`` / ``is_knowledge_bearing``
    (both the old classified shape and C2 have them)."""

    return [r["thread_id"] for r in classified["threads"] if r.get("is_knowledge_bearing")]


class Extractor:
    """One step-3 run: decides per thread what to do, calls the model, assembles the output."""

    def __init__(
        self, llm: LLMClient, logger: logging.Logger, *, use_batch: bool, previous: dict[str, Any]
    ) -> None:
        self.llm = llm
        self.logger = logger
        self.use_batch = use_batch and llm.supports_batch()
        meta = previous.get("metadata") or {}
        self.prev_inputs: dict[str, str] = meta.get("inputs") or {}
        self.prev_messages: dict[str, dict[str, str]] = meta.get("message_fingerprints") or {}
        self.prev_claims: dict[str, list[dict[str, Any]]] = {}
        for claim in previous.get("claims") or []:
            self.prev_claims.setdefault(claim["thread_id"], []).append(claim)
        self.failed: list[str] = []
        self.ungrounded = 0
        self.invalid_counters = 0

    def plan(self, thread: dict[str, Any], *, changed: bool) -> ThreadClaims | None:
        """The thread's model job, or ``None`` when its claims only need recomputing."""

        thread_id = thread["thread_id"]
        old = self.prev_claims.get(thread_id, [])
        if not changed:
            return None
        if not old:
            return ThreadClaims(thread)
        before = self.prev_messages.get(thread_id, {})
        current = message_fingerprints(thread)
        new_ids = frozenset(i for i, fp in current.items() if before.get(i) != fp)
        return ThreadClaims(thread, new_ids) if new_ids else None

    def ask(self, jobs: list[ThreadClaims]) -> dict[str, Any]:
        """thread_id -> parsed model answer; failed threads are left out (and logged)."""

        if not jobs:
            return {}
        schemas = {
            job.thread_id: UPDATE_SCHEMA if self.prev_claims.get(job.thread_id) else FRESH_SCHEMA
            for job in jobs
        }
        prompts = {
            job.thread_id: job.prompt(self.prev_claims.get(job.thread_id, [])) for job in jobs
        }
        if self.use_batch:
            self.logger.info(f"Extract: {len(jobs)} threads via the batch API")
            return self.llm.complete_batch_json(
                [
                    BatchRequest(
                        request_id=tid,
                        system=EXTRACT_SYSTEM,
                        user=prompts[tid],
                        task="extract",
                        response_schema=schemas[tid],
                    )
                    for tid in prompts
                ]
            )
        answers: dict[str, Any] = {}
        for tid, prompt in prompts.items():
            try:
                answers[tid] = self.llm.complete_json(
                    EXTRACT_SYSTEM, prompt, task="extract", response_schema=schemas[tid]
                )
            # Provider errors have no common base class; one thread must not stop the run.
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"Extract: model call failed for {tid}: {exc!r}")
        return answers

    def absorb(self, job: ThreadClaims, answer: Any) -> list[dict[str, Any]] | None:
        """The thread's claim records after the answer, or ``None`` if it is unusable."""

        try:
            records = job.absorb(answer, self.prev_claims.get(job.thread_id, []))
        except OutputError as exc:
            self.logger.warning(f"Extract: {job.thread_id}: {exc}")
            return None
        self.ungrounded += job.ungrounded
        self.invalid_counters += len(job.invalid_counters)
        for problem in job.invalid_counters:
            self.logger.info(f"Extract: {job.thread_id}: counter reference dropped: {problem}")
        return records

    def counter_summary(self, claims: list[dict[str, Any]]) -> dict[str, int]:
        """Counter links of the claims of the threads the model read, and where they disagree
        with the opposing evidence (``check_counters``, per thread); each case is logged."""

        by_thread: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            by_thread.setdefault(claim["thread_id"], []).append(claim)
        totals = {"counter_links": 0, **{kind: 0 for kind in check_counters([])}}
        for thread_id, thread_claims in by_thread.items():
            totals["counter_links"] += sum(len(c["counters"]) for c in thread_claims) // 2
            for kind, cases in check_counters(thread_claims).items():
                totals[kind] += len(cases)
                for case in cases:
                    self.logger.info(f"Extract: {thread_id}: {kind}: {case}")
        return totals


def run(
    *,
    threads_path: Path | str | None = None,
    classified_path: Path | str | None = None,
    export_path: Path | str | None = CHAT_ANDROID_PATH,
    artifact: Artifact = CLAIMS,
    audit_path: Path | str = AUDIT_PATH,
    llm: LLMClient | None = None,
    use_batch: bool = True,
    only: Collection[str] | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    """Extract / update claims; see the module docstring.

    ``threads_path`` / ``classified_path`` default to the reviewed (edited) files when they
    exist. ``export_path`` is the chat export quotes and reactions are joined from (``None``
    when the threads carry them inline). ``only`` restricts the work to these thread ids
    (everything else is carried as it is); ``rebuild`` ignores the previous output.
    """

    logger = setup_step_logging(STEP_3)
    llm = LLMClient.for_stage("extract", logger=logger) if llm is None else llm
    llm.set_logger(logger)
    audit_path = Path(audit_path)

    threads = {
        t["thread_id"]: t
        for t in load_threads(threads_path or resolve_threads_path())["threads"]
    }
    with Path(classified_path or resolve_classified_path()).open(encoding="utf-8") as f:
        knowledge = knowledge_thread_ids(json.load(f))
    unknown = [tid for tid in knowledge if tid not in threads]
    if unknown:
        logger.warning(
            f"Extract: {len(unknown)} knowledge-bearing ids not in threads: {unknown[:10]}"
        )
    export_index = load_export_index(export_path) if export_path is not None else None
    scope = None if only is None else set(only)
    current = {
        tid: with_export(threads[tid], export_index)
        for tid in knowledge
        if tid in threads and (scope is None or tid in scope)
    }

    previous = {} if rebuild else load_previous(artifact)
    extractor = Extractor(llm, logger, use_batch=use_batch, previous=previous)
    inputs = {tid: input_fingerprint(thread) for tid, thread in current.items()}
    diff = diff_inputs(
        {tid: fp for tid, fp in extractor.prev_inputs.items() if scope is None or tid in scope},
        inputs,
    )

    # Out of scope (``only``): carried exactly as they are, fingerprints included.
    def out_of_scope(thread_id: str) -> bool:
        return scope is not None and thread_id not in scope

    out_inputs = {t: fp for t, fp in extractor.prev_inputs.items() if out_of_scope(t)}
    out_messages = {
        t: fps for t, fps in extractor.prev_messages.items() if out_of_scope(t)
    }
    carried_threads = [tid for tid in extractor.prev_claims if out_of_scope(tid)]

    jobs: dict[str, ThreadClaims] = {}
    for tid, thread in current.items():
        job = extractor.plan(thread, changed=tid in diff.new or tid in diff.changed)
        if job is not None:
            jobs[tid] = job
    answers = extractor.ask(list(jobs.values()))

    records: dict[str, list[dict[str, Any]]] = {}
    fresh: list[dict[str, Any]] = []
    for tid, thread in current.items():
        old = extractor.prev_claims.get(tid, [])
        records[tid] = old
        if tid in jobs:
            absorbed = extractor.absorb(jobs[tid], answers[tid]) if tid in answers else None
            if absorbed is None:
                extractor.failed.append(tid)
                if tid in extractor.prev_inputs:  # retried next run
                    out_inputs[tid] = extractor.prev_inputs[tid]
                    out_messages[tid] = extractor.prev_messages.get(tid, {})
                continue
            known = {c["claim_id"] for c in old}
            fresh.extend(c for c in absorbed if c["claim_id"] not in known)
            records[tid] = absorbed
        out_inputs[tid] = inputs[tid]
        out_messages[tid] = message_fingerprints(thread)
    scrub_summary = scrub_claims(fresh)

    claims: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    dropped = 0
    for tid, thread_records in records.items():
        kept, lost = (jobs.get(tid) or ThreadClaims(current[tid])).finalize_all(thread_records)
        dropped += lost
        claims.extend(claim for claim, _ in kept)
        audit.extend(record for _, record in kept)
    read = set(jobs) - set(extractor.failed)
    counter_check = extractor.counter_summary([c for c in claims if c["thread_id"] in read])
    previous_audit = load_audit(audit_path) if carried_threads else {}
    for tid in carried_threads:
        for claim in extractor.prev_claims[tid]:
            claims.append(claim)
            if claim["claim_id"] in previous_audit:
                audit.append(previous_audit[claim["claim_id"]])

    stats = {
        "threads_in_scope": len(current),
        "model_calls": len(jobs),
        "new_threads": len(diff.new),
        "changed_threads": len(diff.changed),
        "removed_threads": len(diff.removed),
        "failed_threads": extractor.failed,
        "new_claims": len(fresh),
        "ungrounded_claims_dropped": extractor.ungrounded,
        "claims_without_evidence_dropped": dropped,
        "invalid_counter_refs_dropped": extractor.invalid_counters,
        "counter_check": counter_check,
    }
    payload = {
        "claims": claims,
        "metadata": {
            "inputs": out_inputs,
            "message_fingerprints": out_messages,
            "provider": llm.provider,
            "model": llm.model,
            "batch_mode": extractor.use_batch,
            "claims_count": len(claims),
            "run": stats,
            "scrub": scrub_summary,
        },
    }
    report = write_reconciled(
        artifact,
        payload,
        items_key="claims",
        id_key="claim_id",
        refresh_keys=DERIVED_KEYS,
        rebuild=rebuild,
    )
    write_audit(audit_path, audit)
    logger.info(
        f"Extract: {len(claims)} claims; {len(jobs)} model calls, {len(fresh)} new claims, "
        f"{len(extractor.failed)} failed threads; {report.changes.summary()}"
    )
    return {
        **stats,
        "claims_count": len(claims),
        "scrub": scrub_summary,
        "changes": dataclasses.asdict(report.changes),
        "backup": str(report.backup) if report.backup else None,
        "audit_path": str(audit_path),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 3: extract claims (incremental).")
    parser.add_argument("--rebuild", action="store_true", help="ignore the previous output")
    parser.add_argument("--only", nargs="+", metavar="THREAD_ID", help="restrict to these threads")
    parser.add_argument("--sync", action="store_true", help="sync API instead of the batch API")
    args = parser.parse_args(argv)
    run(use_batch=not args.sync, only=args.only, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
