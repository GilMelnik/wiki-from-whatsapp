"""Side script: re-classify threads whose classification silently failed.

Step 2 writes a record for every thread. A thread sent to the classifier that
errors (an LLM exception, or a batch item that never parsed) is recorded with a
``reason`` beginning ``classification_error:`` and ``is_knowledge_bearing:
null`` — which excludes it from extraction with no other signal. A failed call
does not mean the thread carries no knowledge, so this finds exactly those
records (by their ``classification_error`` reason) and re-classifies only them,
merging results back into the classified file(s) the rest of the pipeline reads.

Re-running is cheap: a previously-failed classification was never written to the
good disk cache, so it re-hits the API, while any thread that already parsed is
served from cache.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from step_2_classify.run import build_classify_prompt, classify_threads
from utils.json_io import write_json_file
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import (
    EDITED_CLASSIFIED_PATH,
    ORIGINAL_CLASSIFIED_PATH,
    STEP_2,
    resolve_classified_path,
)
from utils.threads_io import (
    DEFAULT_THREADS_PATH,
    load_threads,
    render_thread_for_llm,
)


def _failed(record: dict[str, Any]) -> bool:
    return str(record.get("reason", "")).startswith("classification_error")


def _merge_into(path: Path, results: dict[str, dict[str, Any]]) -> int:
    """Apply recovered classifications to ``path``, only for records still failed.

    Guarding on ``_failed`` keeps human edits in the reviewed file intact and
    makes reruns idempotent. Returns how many records were updated.
    """

    with path.open(encoding="utf-8") as f:
        payload = json.load(f)

    updated = 0
    for record in payload.get("threads", []):
        result = results.get(record["thread_id"])
        if result is not None and _failed(record) and not _failed(result):
            record.update(result)
            updated += 1

    payload.setdefault("metadata", {})["knowledge_bearing_count"] = sum(
        1 for r in payload.get("threads", []) if r.get("is_knowledge_bearing")
    )
    write_json_file(payload, path)
    return updated


def run(
    input_path: Path | str = DEFAULT_THREADS_PATH,
    classified_path: Path | str | None = None,
    llm: LLMClient | None = None,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Re-classify threads whose ``reason`` marks a failed classification."""

    logger = setup_step_logging(STEP_2)
    classified_path = (
        Path(classified_path) if classified_path is not None else resolve_classified_path()
    )
    if not classified_path.is_file():
        raise FileNotFoundError(
            f"{classified_path} not found. Run step_2_classify first before backfilling."
        )

    llm = LLMClient.for_stage("classify", logger=logger) if llm is None else llm
    llm.set_logger(logger)
    threads_by_id = {t["thread_id"]: t for t in load_threads(input_path)["threads"]}

    with classified_path.open(encoding="utf-8") as f:
        classified = json.load(f)

    pending_llm: list[tuple[dict[str, Any], str]] = []
    failed_ids: list[str] = []
    for record in classified["threads"]:
        if not _failed(record):
            continue
        thread_id = record["thread_id"]
        failed_ids.append(thread_id)
        thread = threads_by_id.get(thread_id)
        if thread is None:
            continue
        rendered, _ = render_thread_for_llm(thread)
        if rendered:
            pending_llm.append((thread, build_classify_prompt(rendered)))

    logger.info(
        f"Rerun: {len(failed_ids)} threads failed classification "
        f"({len(pending_llm)} with renderable content)."
    )
    if not pending_llm:
        return {"failed_threads": len(failed_ids), "recovered_threads": 0}

    results = classify_threads(pending_llm, llm, use_batch, logger)
    recovered = sum(
        1 for thread, _ in pending_llm if not _failed(results[thread["thread_id"]])
    )

    for path in {resolve_classified_path(), ORIGINAL_CLASSIFIED_PATH, EDITED_CLASSIFIED_PATH}:
        if path.is_file():
            _merge_into(path, results)

    logger.info(
        f"Rerun: recovered {recovered}/{len(failed_ids)} failed classifications."
    )
    return {"failed_threads": len(failed_ids), "recovered_threads": recovered}


if __name__ == "__main__":
    run()
