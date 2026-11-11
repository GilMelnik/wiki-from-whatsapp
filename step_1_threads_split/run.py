"""Step 1: split the chat export into conversation threads (contract C1).

``python -m step_1_threads_split.run [--rebuild]`` reads the export (``CHAT_ANDROID_PATH``),
keeps every message with content (``step_1_threads_split.message``) and splits them with
``step_1_threads_split.splitter``. Output ``threads.json`` (and ``threads_edited.json`` when
the owner has edited the threads)::

    {"threads": [{thread_id, start_time, last_time, participants, message_ids,
                  num_messages, num_unique_senders, last_sender,
                  messages: [{id, datetime, sender, content, quote?, reactions?}]}],
     "metadata": {format, source, inputs: {wa_id: fingerprint}, params, embedding,
                  git_commit, generated_at, run, message_count, thread_count}}

Reconcile (C14, custom merge, like step 4): ``metadata.inputs`` holds a fingerprint per
message. A run starts from the current EDITED threads when they exist (else the step's own
output) and applies only what changed in the export: removed messages leave their threads
(empty threads disappear), changed messages (new reactions, edited text) are refreshed where
they are, new messages are placed with ``ThreadSplitter.assign_new_messages``. Threads that
nothing touched stay exactly as they are, manual edits included. With an edited file both
files are written (edited first, after a backup); a run with nothing new writes nothing.
``--rebuild`` splits everything from scratch after backing up both files (an existing edited
file is replaced, so it cannot shadow the fresh split).

A threads file without ``metadata.format`` equal to ``threads.format`` (the old splitter's
files, the owner's 2026-11-11 tagging) is never used as a starting point: the run stops and
asks to move it away or to ``--rebuild``.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections.abc import Sequence
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from step_1_threads_split.message import message_record
from step_1_threads_split.splitter import EmbedFn, SplitParams, ThreadSplitter, thread_record
from utils.config import section
from utils.embedder import embed_texts
from utils.embedding_ab import _git_commit
from utils.logging_setup import setup_step_logging
from utils.paths import CHAT_ANDROID_PATH, STEP_1, THREADS, Artifact
from utils.reconcile import diff_inputs, fingerprint, guarded_write

THREADS_CONFIG = section("threads")
EMBEDDING_CONFIG = section("embedding")


def load_export_messages(export_path: Path) -> list[dict[str, Any]]:
    """The C1 records of every export message with content, in export (time) order."""
    if not export_path.is_file():
        raise FileNotFoundError(f"chat export not found: {export_path}")
    entries = json.loads(export_path.read_text(encoding="utf-8"))
    records = [record for record in map(message_record, entries) if record is not None]
    return sorted(records, key=lambda record: record["datetime"])


def _read_threads(path: Path) -> dict[str, Any]:
    """A C1 threads file written by this step, ``{}`` when absent; raises on any other file."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = payload.get("metadata", {}).get("format")
    if found != THREADS_CONFIG["format"]:
        raise ValueError(
            f"{path} is not a threads file of this step (metadata.format is {found!r}, expected "
            f"{THREADS_CONFIG['format']!r}); it is probably the old splitter's output or the "
            "old tagging. Move it out of the step folder, or run with --rebuild (it is backed "
            "up first)."
        )
    return payload


def _apply_export_changes(
    threads: list[dict[str, Any]], current: dict[str, dict[str, Any]], changed: set[str]
) -> tuple[list[dict[str, Any]], int]:
    """Drop messages gone from the export and refresh changed ones, keeping each in place."""
    kept: list[dict[str, Any]] = []
    touched = 0
    for thread in threads:
        messages = [
            current[m["id"]] if m["id"] in changed else m
            for m in thread["messages"]
            if m["id"] in current
        ]
        if messages == thread["messages"]:
            kept.append(thread)
            continue
        touched += 1
        if messages:
            kept.append(thread_record(thread["thread_id"], messages))
    return kept, touched


def run(
    *,
    export_path: Path = CHAT_ANDROID_PATH,
    artifact: Artifact = THREADS,
    rebuild: bool = False,
    embed: EmbedFn | None = None,
) -> dict[str, Any]:
    """Bring the threads up to date with the export; see the module docstring.

    ``embed`` defaults to ``utils.embedder.embed_texts`` with ``threads.embedding_kind``.
    Returns the run counters (``metadata.run``) plus ``written`` and ``backup``.
    """
    logger = setup_step_logging(STEP_1)
    random.seed(0)
    np.random.seed(0)
    started = time.monotonic()
    params = SplitParams.from_config()
    splitter = ThreadSplitter(
        params, embed or partial(embed_texts, kind=THREADS_CONFIG["embedding_kind"])
    )
    messages = load_export_messages(export_path)
    current = {m["id"]: m for m in messages}
    inputs = {wa_id: fingerprint(m) for wa_id, m in current.items()}

    previous = {} if rebuild else _read_threads(artifact.original)
    edited = {} if rebuild else _read_threads(artifact.edited)
    basis = edited or previous
    diff = diff_inputs(previous.get("metadata", {}).get("inputs", {}), inputs)
    stats = {"new": len(diff.new), "changed": len(diff.changed), "removed": len(diff.removed)}
    if basis and not (diff.new or diff.changed or diff.removed):
        logger.info(f"Threads: {len(messages)} messages unchanged; nothing to do")
        return {**stats, "threads_touched": 0, "written": False, "backup": None}
    if basis and basis["metadata"].get("params") != params.to_dict():
        logger.warning(
            f"threads.split changed since the last run ({basis['metadata'].get('params')} -> "
            f"{params.to_dict()}); existing threads are kept, only new messages use the new "
            "values. Run with --rebuild to re-split everything."
        )

    if basis:
        threads, touched = _apply_export_changes(basis["threads"], current, set(diff.changed))
        before = {t["thread_id"] for t in threads}
        threads = splitter.assign_new_messages(threads, [current[i] for i in diff.new])
        stats["threads_touched"] = touched
        stats["threads_created"] = len({t["thread_id"] for t in threads} - before)
    else:
        threads = splitter.split(messages)
        stats["threads_touched"] = 0
        stats["threads_created"] = len(threads)

    payload = {
        "threads": threads,
        "metadata": {
            "format": THREADS_CONFIG["format"],
            "source": str(export_path),
            "inputs": inputs,
            "params": params.to_dict(),
            "embedding": {
                "model": EMBEDDING_CONFIG["model"],
                "dimensions": EMBEDDING_CONFIG["output_dimensionality"],
                "kind": THREADS_CONFIG["embedding_kind"],
                "max_chars": THREADS_CONFIG["embed_max_chars"],
            },
            "git_commit": _git_commit(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "run": stats,
            "message_count": sum(len(t["messages"]) for t in threads),
            "thread_count": len(threads),
        },
    }
    backup = None
    if artifact.edited.is_file():
        backup = guarded_write(artifact.edited, payload, backup=True)
    guarded_write(artifact.original, payload, backup=rebuild)
    logger.info(
        f"Threads: {payload['metadata']['thread_count']} threads, "
        f"{payload['metadata']['message_count']} messages; run {stats}; "
        f"params {params.to_dict()}; commit {payload['metadata']['git_commit']}; "
        f"{time.monotonic() - started:.1f} s"
    )
    return {**stats, "written": True, "backup": str(backup) if backup else None}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_1_threads_split.run",
        description="Step 1: split the chat export into threads (incremental).",
    )
    parser.add_argument("--rebuild", action="store_true", help="re-split everything")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
