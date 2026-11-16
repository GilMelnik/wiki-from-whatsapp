"""Keep step 2's answers when ``config.json`` gives the same classifier model another name.

The model's name is part of every conversation's input fingerprint
(``ThreadClassifier.settings``), so replacing an alias by the exact version that answered
behind it would make step 2 ask about every conversation it once answered. This command keeps
those answers instead: every conversation whose record still holds under the stored settings
gets the fingerprint of the current settings, and the metadata of both files takes the new
name. A conversation whose record no longer held (its messages changed) keeps its old
fingerprint: it needs a label as before, unless a hand label of the owner covers it. The
records themselves, and so the owner's hand labels (which never depend on the settings), are
not touched.

It refuses, writing nothing, unless the stored model is the ``OLD_MODEL`` given and the
model's name is the only setting that differs from ``config.json``: any other change of the
settings changes answers, and a normal run of step 2 must ask again. Both files are backed
up (``utils.reconcile.backup_file``) before they are written, and keep their modification
time: no answer changed, so nothing built from them is out of date (the review site judges
that by the files' times).

Use it only for a rename of the same model. A different model must answer again.

``python -m step_2_classify.rename_model OLD_MODEL``
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from typing import Any

from step_2_classify.labels import LabelRule
from step_2_classify.run import ThreadClassifier
from utils.file_lock import file_lock
from utils.json_io import read_json_if_present, write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import CLASSIFIED, STEP_2, THREADS, resolve
from utils.reconcile import backup_file, load_previous
from utils.threads_io import load_threads


def _require_rename_only(
    stored: dict[str, Any], current: dict[str, Any], old_model: str
) -> None:
    """Raise unless ``stored`` is ``current`` with the classifier's model named ``old_model``."""
    expected = {**current, "classifier": {**current["classifier"], "model": old_model}}
    if stored == expected:
        return
    stored_classifier = stored.get("classifier") or {}
    differing = sorted(key for key in current if key != "classifier" and stored.get(key) != current[key])
    differing += sorted(
        f"classifier.{key}"
        for key in current["classifier"]
        if key != "model" and stored_classifier.get(key) != current["classifier"][key]
    )
    raise ValueError(
        f"not a rename of the model alone: step 2's records were made by "
        f"{stored_classifier.get('model')!r} (given: {old_model!r}); other settings that differ "
        f"from config.json: {differing or 'none'}. Nothing was written."
    )


def rename_model(old_model: str) -> dict[str, int]:
    """Carry the stored fingerprints over to the model name now in ``config.json``.

    Returns ``{"carried": n, "left": m}``: the conversations whose fingerprint was rewritten,
    and the ones left as they were (their record no longer held under the old name either).
    Raises ``ValueError`` (nothing written) if step 2 has no output or the change is not a
    rename of the model alone.
    """
    logger = setup_step_logging(STEP_2)
    decider = ThreadClassifier.from_config(logger)
    current = decider.settings()
    threads = {thread["thread_id"]: thread for thread in load_threads(resolve(THREADS))["threads"]}
    with file_lock(CLASSIFIED.original), file_lock(CLASSIFIED.edited):
        original = load_previous(CLASSIFIED)
        if not original:
            raise ValueError(f"step 2 has no output to carry over: {CLASSIFIED.original}")
        edited = read_json_if_present(CLASSIFIED.edited)
        rule = LabelRule(original, edited)
        if rule.stored_settings == current:
            logger.info(f"Rename model: the records already carry {current['classifier']['model']!r}")
            return {"carried": 0, "left": 0}
        _require_rename_only(rule.stored_settings, current, old_model)

        holding = {
            thread_id
            for thread_id in rule.inputs
            if thread_id in threads and rule.own_holds(threads[thread_id])
        }
        inputs = {
            thread_id: decider.fingerprint(threads[thread_id]) if thread_id in holding else stored
            for thread_id, stored in rule.inputs.items()
        }
        backups = []
        for path, payload in ((CLASSIFIED.original, original), (CLASSIFIED.edited, edited)):
            if payload is None:
                continue
            backups.append(backup_file(path))
            times = path.stat()
            metadata = {**payload.get("metadata", {}), **current, "inputs": inputs}
            write_json_file({**payload, "metadata": metadata}, path)
            os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    report = {"carried": len(holding), "left": len(inputs) - len(holding)}
    logger.info(
        f"Rename model: {old_model!r} -> {current['classifier']['model']!r}; fingerprints "
        f"carried over: {report['carried']}; left as they were (their record no longer held): "
        f"{report['left']}; backups: {[str(backup) for backup in backups]}"
    )
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_2_classify.rename_model",
        description="Keep step 2's answers after the classifier's model was renamed in config.json.",
    )
    parser.add_argument("old_model", metavar="OLD_MODEL", help="the name the records were made under")
    args = parser.parse_args(argv)
    rename_model(args.old_model)


if __name__ == "__main__":
    main()
