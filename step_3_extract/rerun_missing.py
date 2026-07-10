"""Side script: re-extract knowledge-bearing threads with no saved claims.

A previous extract run can silently drop a thread whose JSON response was
truncated at ``max_tokens`` — such responses are never cached and are only
recorded to ``data/llm_failures.jsonl``. This finds every knowledge-bearing
thread that has *zero* claims in ``data/claims.json`` and re-runs extraction for
just those, merging any new claims/audit back into the existing outputs.

Re-running is cheap for genuinely-empty threads (their valid ``{"claims": []}``
response is served from the disk cache); only the threads that actually failed
last time hit the API again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from step_3_extract.run import (
    DEFAULT_CLASSIFIED_PATH,
    build_extract_prompt,
    extract_claims_for_threads,
    _knowledge_bearing_ids,
)
from step_3_extract.scrub import scrub_claims
from utils.json_io import write_json_file
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import AUDIT_DIR, STEP_3, resolve_claims_path
from utils.threads_io import (
    DEFAULT_THREADS_PATH,
    load_threads,
    render_thread_for_llm,
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def run(
    input_path: Path | str = DEFAULT_THREADS_PATH,
    classified_path: Path | str = DEFAULT_CLASSIFIED_PATH,
    output_path: Path | str | None = None,
    audit_dir: Path | str = AUDIT_DIR,
    llm: LLMClient | None = None,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Fill in claims for knowledge-bearing threads missing from ``output_path``.

    ``output_path`` defaults to the human-reviewed ``claims_edited.json`` when it
    exists (falling back to the pipeline original), so backfilled claims land in
    the file the rest of the pipeline reads.
    """

    logger = setup_step_logging(STEP_3)
    output_path = Path(output_path) if output_path is not None else resolve_claims_path()
    if not output_path.is_file():
        raise FileNotFoundError(
            f"{output_path} not found. Run step_3_extract first before backfilling."
        )

    llm = LLMClient.for_stage("extract", logger=logger) if llm is None else llm
    llm.set_logger(logger)
    threads_by_id = {t["thread_id"]: t for t in load_threads(input_path)["threads"]}
    classified = _load_json(Path(classified_path))
    keep_ids = _knowledge_bearing_ids(classified, None)

    existing = _load_json(output_path)
    saved_ids = {claim["thread_id"] for claim in existing.get("claims", [])}

    pending_llm: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    missing_ids: list[str] = []
    for record in classified["threads"]:
        thread_id = record["thread_id"]
        if thread_id not in keep_ids or thread_id in saved_ids:
            continue
        missing_ids.append(thread_id)
        rendered, line_meta = render_thread_for_llm(threads_by_id[thread_id])
        if rendered:
            pending_llm.append(
                (
                    threads_by_id[thread_id],
                    build_extract_prompt(
                        rendered, entities_hint=record.get("entities") or []
                    ),
                    line_meta,
                )
            )

    logger.info(
        f"Rerun: {len(missing_ids)} knowledge-bearing threads have no saved claims "
        f"({len(pending_llm)} with renderable content)."
    )
    if not pending_llm:
        return {"missing_threads": len(missing_ids), "new_claims": 0, "recovered_threads": 0}

    new_claims, new_audit = extract_claims_for_threads(pending_llm, llm, use_batch, logger)
    scrub_summary = scrub_claims(new_claims)
    recovered = {claim["thread_id"] for claim in new_claims}

    existing["claims"] = existing.get("claims", []) + new_claims
    metadata = existing.setdefault("metadata", {})
    metadata["claims_count"] = len(existing["claims"])
    write_json_file(existing, output_path)

    audit_path = Path(audit_dir) / "claims_audit.json"
    audit_doc = _load_json(audit_path) if audit_path.is_file() else {"audit": []}
    audit_doc["audit"] = audit_doc.get("audit", []) + new_audit
    audit_doc.setdefault("metadata", {})["claims_count"] = len(audit_doc["audit"])
    write_json_file(audit_doc, audit_path)

    logger.info(
        f"Rerun: recovered {len(recovered)}/{len(missing_ids)} threads, "
        f"added {len(new_claims)} claims."
    )
    return {
        "missing_threads": len(missing_ids),
        "recovered_threads": len(recovered),
        "new_claims": len(new_claims),
        "scrub": scrub_summary,
    }


if __name__ == "__main__":
    run()
