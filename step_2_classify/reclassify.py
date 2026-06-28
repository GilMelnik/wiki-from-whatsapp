"""Re-run topic classification on edited threads after taxonomy changes.

Reads ``data/threads_edited.json`` and ``data/threads_classified_edited.json``.
Threads with ``is_knowledge_bearing: false`` are left unchanged (manual review).
All other threads are sent through the classifier again and written back to
``data/threads_classified_edited.json``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.json_io import write_json_file
from utils.paths import (
    BACKUPS_DIR,
    EDITED_CLASSIFIED_PATH,
    EDITED_THREADS_PATH,
)
from step_2_classify.run import (
    CLASSIFY_SYSTEM,
    build_classify_prompt,
    classify_from_text,
    classify_thread,
)
from utils.llm_client import BatchRequest, LLMClient
from utils.threads_io import load_threads, render_thread_for_llm


def _sync_record_stats(thread: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    record.update(
        {
            "thread_id": thread["thread_id"],
            "start_time": thread["start_time"],
            "last_time": thread["last_time"],
            "num_messages": thread["num_messages"],
            "num_unique_senders": thread["num_unique_senders"],
        }
    )
    return record


def _base_record(thread: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    record = _sync_record_stats(thread, {})
    record["passed_heuristic"] = True
    if existing:
        for key in (
            "is_knowledge_bearing",
            "topic_tags",
            "emergent_tags",
            "entities",
            "reason",
            "passed_heuristic",
        ):
            if key in existing:
                record[key] = existing[key]
    return record


def _should_skip(existing: dict[str, Any] | None) -> bool:
    if existing is None:
        return False
    return existing.get("is_knowledge_bearing") is False


def _backup_classified(path: Path) -> None:
    if not path.is_file():
        return
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, BACKUPS_DIR / f"{path.stem}_{ts}{path.suffix}")


def run(
    threads_path: Path | str = EDITED_THREADS_PATH,
    classified_path: Path | str = EDITED_CLASSIFIED_PATH,
    llm: LLMClient | None = None,
    max_threads: int | None = None,
    use_batch: bool = False,
) -> dict[str, Any]:
    """Re-classify edited threads, preserving manual ``is_knowledge_bearing: false``."""

    threads_path = Path(threads_path)
    classified_path = Path(classified_path)
    if not threads_path.is_file():
        raise FileNotFoundError(f"Edited threads not found: {threads_path}")
    if not classified_path.is_file():
        raise FileNotFoundError(
            f"Edited classifications not found: {classified_path}. "
            "Run classify or init the review workspace first."
        )

    llm = llm or LLMClient()
    threads_payload = load_threads(threads_path)
    with classified_path.open(encoding="utf-8") as f:
        classified_payload = json.load(f)

    existing_by_id = {
        record["thread_id"]: record for record in classified_payload.get("threads", [])
    }

    output_records: list[dict[str, Any]] = []
    pending_llm: list[tuple[dict[str, Any], str]] = []
    pending_indices: list[int] = []
    skipped = 0
    sent_to_llm = 0

    for thread in threads_payload["threads"]:
        thread_id = thread["thread_id"]
        existing = existing_by_id.get(thread_id)
        record = _base_record(thread, existing)

        if _should_skip(existing):
            output_records.append(record)
            skipped += 1
            continue

        if max_threads is not None and sent_to_llm >= max_threads:
            output_records.append(record)
            continue

        rendered, _ = render_thread_for_llm(thread)
        prompt = build_classify_prompt(rendered)
        pending_llm.append((thread, prompt))
        pending_indices.append(len(output_records))
        output_records.append(record)
        sent_to_llm += 1

    if pending_llm:
        if use_batch and llm.supports_batch():
            print(f"  Reclassify: submitting {len(pending_llm)} requests via batch API...")
            batch_results = llm.complete_batch(
                [
                    BatchRequest(
                        request_id=thread["thread_id"],
                        system=CLASSIFY_SYSTEM,
                        user=prompt,
                        task="classify",
                    )
                    for thread, prompt in pending_llm
                ]
            )
            for (thread, _), record_idx in zip(pending_llm, pending_indices):
                output_records[record_idx].update(
                    classify_from_text(batch_results.get(thread["thread_id"], ""))
                )
        else:
            if use_batch:
                print("  Reclassify: batch not supported for this provider; using sync API.")
            for (thread, _), record_idx in zip(pending_llm, pending_indices):
                output_records[record_idx].update(classify_thread(thread, llm))

    metadata = dict(classified_payload.get("metadata") or {})
    metadata.update(
        {
            "source_threads": str(threads_path),
            "thread_count": len(output_records),
            "knowledge_bearing_count": sum(
                1 for record in output_records if record.get("is_knowledge_bearing")
            ),
            "reclassified_by_llm": sent_to_llm,
            "skipped_not_knowledge_bearing": skipped,
            "reclassified_at": datetime.now().isoformat(timespec="seconds"),
            "provider": llm.provider,
            "model": llm.model,
            "batch_mode": use_batch and llm.supports_batch(),
        }
    )

    output = {"threads": output_records, "metadata": metadata}
    _backup_classified(classified_path)
    write_json_file(output, classified_path)
    return metadata
