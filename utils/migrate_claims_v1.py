"""Convert v1 step-3 claims (backup) into C3 claims + the private audit, without any LLM call.

    python -m utils.migrate_claims_v1 --backup-dir <v1 data dir> --out <new data dir>

Reads, under ``--backup-dir`` (a v1 data dir, laid out like ``utils.paths``): the claims
(``claims_edited.json`` if present, else ``claims.json``), the private audit, the threads
(edited if present) and the chat export. Writes, under ``--out``, the same layout as a step-3
run would: ``step_3_extract/claims.json`` (C3, with ``metadata.inputs`` /
``message_fingerprints`` so a later run on the same threads makes no model calls for them)
and ``step_3_extract/audit/claims_audit.json``. ``--out`` is required so the canonical data
dir is never written by accident.

v1 audit ``local_message_ids`` index the thread's messages; they become WhatsApp ids. The
claim id is computed from the text as the model extracted it (audit ``raw_claim_text``); the
reviewed text of the claims file is kept as ``claim_text``. Identical texts in one thread
collapse into one claim. ``metadata.legacy_claim_ids`` maps each new id to its v1 ids. A v1
claim the old model did not tie to any message is kept with empty evidence, zero counts and
its thread's date span (``metadata.migration.claims_without_evidence``). v1 did not link a
dissent to the claim it disputes, so every migrated claim has ``counters: []``.
``participant_count`` (and the audit's ``participants``) come from the backup's thread, as
step 3 computes them: the distinct people who wrote in it.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from step_3_extract.claims import (
    ThreadClaims,
    claim_id_for,
    input_fingerprint,
    message_fingerprints,
)
from step_3_extract.prompt import STANCES
from step_3_extract.run import write_audit
from utils.config import DATA_DIR
from utils.json_io import write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import AUDIT_PATH, CHAT_ANDROID_PATH, CLAIMS, STEP_3, THREADS, Artifact
from utils.threads_io import load_export_index, load_threads, with_export


def _under(root: Path, path: Path) -> Path:
    """``path`` (a location in this project's data dir) re-rooted at ``root``."""
    return root / path.relative_to(DATA_DIR)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _reviewed(root: Path, artifact: Artifact) -> Path:
    edited = _under(root, artifact.edited)
    return edited if edited.is_file() else _under(root, artifact.original)


def _answer(
    old_claims: list[dict[str, Any]],
    audit: dict[str, dict[str, Any]],
    thread: dict[str, Any],
    refs: list[str],
    counts: Counter[str],
) -> dict[str, Any]:
    """The v1 claims of one thread rephrased as a fresh model answer (numbers = rendering)."""

    number_of = {message_id: n for n, message_id in enumerate(refs, start=1)}
    messages = thread["messages"]

    def numbers(local_ids: list[int]) -> list[int]:
        out = []
        for index in local_ids:
            if 0 <= index < len(messages) and str(messages[index]["id"]) in number_of:
                out.append(number_of[str(messages[index]["id"])])
            else:
                counts["evidence_ids_dropped"] += 1
        return out

    answer = []
    for claim in old_claims:
        record = audit[claim["claim_id"]]
        answer.append(
            {
                "text": record.get("raw_claim_text") or claim["claim_text"],
                "entities": [e for e in claim.get("entities") or [] if isinstance(e, str)],
                "stance": claim.get("stance") if claim.get("stance") in STANCES else "neutral",
                "supporting": numbers(record.get("local_message_ids") or []),
                "opposing": numbers(record.get("opposing_local_message_ids") or []),
                "counters": [],
            }
        )
    return {"claims": answer}


def migrate(backup_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Write the C3 claims + audit for the v1 corpus in ``backup_dir`` under ``out_dir``."""

    logger = setup_step_logging(STEP_3)
    old = _read_json(_reviewed(backup_dir, CLAIMS))["claims"]
    audit = {r["claim_id"]: r for r in _read_json(_under(backup_dir, AUDIT_PATH))["audit"]}
    threads = {
        t["thread_id"]: t for t in load_threads(_reviewed(backup_dir, THREADS))["threads"]
    }
    export_index = load_export_index(_under(backup_dir, CHAT_ANDROID_PATH))

    counts: Counter[str] = Counter(v1_claims=len(old))
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for claim in old:
        if claim["claim_id"] not in audit:
            counts["skipped_no_audit"] += 1
        elif claim["thread_id"] not in threads:
            counts["skipped_no_thread"] += 1
        else:
            by_thread.setdefault(claim["thread_id"], []).append(claim)

    claims: list[dict[str, Any]] = []
    audit_out: list[dict[str, Any]] = []
    legacy: dict[str, list[str]] = {}
    inputs: dict[str, str] = {}
    fingerprints: dict[str, dict[str, str]] = {}
    for thread_id, old_claims in by_thread.items():
        thread = with_export(threads[thread_id], export_index)
        job = ThreadClaims(thread)
        answer = _answer(old_claims, audit, threads[thread_id], job.refs, counts)
        records = job.absorb(answer, [], require_evidence=False)
        counts["skipped_empty_text"] += job.ungrounded
        by_id = {r["claim_id"]: r for r in records}
        for old_claim, raw in zip(old_claims, answer["claims"]):
            record = by_id.get(claim_id_for(thread_id, raw["text"].strip()))
            if record is None:
                continue
            legacy.setdefault(record["claim_id"], []).append(old_claim["claim_id"])
            if len(legacy[record["claim_id"]]) == 1:
                record["claim_text"] = old_claim["claim_text"]  # the reviewed text
                if old_claim.get("_redactions"):
                    record["_redactions"] = old_claim["_redactions"]
        kept, _ = job.finalize_all(records)
        claims.extend(claim for claim, _ in kept)
        audit_out.extend(record for _, record in kept)
        inputs[thread_id] = input_fingerprint(thread)
        fingerprints[thread_id] = message_fingerprints(thread)

    counts["c3_claims"] = len(claims)
    counts["merged_duplicates"] = sum(len(ids) - 1 for ids in legacy.values())
    counts["threads"] = len(by_thread)
    counts["claims_with_opposing"] = sum(1 for c in claims if c["opposing_message_ids"])
    counts["claims_without_evidence"] = sum(1 for c in claims if not c["supporting_message_ids"])
    summary = dict(counts)
    write_json_file(
        {
            "claims": claims,
            "metadata": {
                "source": "migrated from v1 by utils.migrate_claims_v1",
                "inputs": inputs,
                "message_fingerprints": fingerprints,
                "claims_count": len(claims),
                "legacy_claim_ids": legacy,
                "migration": summary,
            },
        },
        _under(out_dir, CLAIMS.original),
    )
    write_audit(_under(out_dir, AUDIT_PATH), audit_out)
    logger.info(f"migrate_claims_v1: {summary} -> {out_dir}")
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backup-dir", type=Path, required=True, help="v1 data dir (read only)")
    parser.add_argument("--out", type=Path, required=True, help="data dir to write C3 files into")
    args = parser.parse_args(argv)
    migrate(args.backup_dir, args.out)


if __name__ == "__main__":
    main()
