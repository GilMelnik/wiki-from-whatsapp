"""The knowledge label of each conversation: step 2's answer, or the owner's own label.

Step 2 (contract C2) answers per conversation whether it carries knowledge worth extracting:
``is_knowledge_bearing``, from the classifier's ``p_knowledge`` (null when the heuristic
rejected the conversation unasked). The owner labels conversations by hand here, in the edited
file (``threads_classified_edited.json``, created from the original by the first label): a
record with ``manual_fingerprint``, the fingerprint of the conversation's messages when it was
labelled. Which label holds and which conversation *needs a label* follows one rule, shared
with step 2's run: ``step_2_classify.labels.LabelRule``. The owner can label any conversation
(also one step 2 never answered, also with step 2's own answer); taking the label away puts
step 2's record back, or leaves the conversation without a label. Nothing here runs step 2.

Use it through a ``StoreHandle`` over both files: reads via ``get()``, ``set`` inside
``mutate()``. Every method takes the conversation itself (a C1 thread), since whether a label
holds depends on the conversation's messages now.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from review_tool.tools.threads.operations import EditRefused
from review_tool.tools.threads.store import EDITED_BY
from step_2_classify.labels import LabelRule, hand_record
from utils.json_io import write_json_file
from utils.reconcile import backup_file
from utils.timestamps import local_now

KnowledgeFilter = Literal["all", "yes", "no", "manual", "needs_label", "changed"]
NOT_RUN = (
    "Step 2 has not classified the conversations yet, so there is no knowledge label to "
    "check. Run step 2 (python -m step_2_classify.run); its answers show here."
)


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("threads"), list):
        raise ValueError(f"{path} is not step 2's output: expected an object with a 'threads' list")
    return payload


def needs_label(label: dict[str, Any] | None) -> bool:
    """No label is in force, or the one in force was given to other messages."""
    return label is None or not label["current"]


def passes(label: dict[str, Any] | None, wanted: KnowledgeFilter, changed: bool) -> bool:
    """Whether the list filter ``wanted`` lets a conversation through; ``changed``: the owner's
    edits created or changed it."""
    if wanted == "all":
        return True
    if wanted == "changed":
        return changed
    if wanted == "needs_label":
        return needs_label(label)
    if label is None:
        return False
    if wanted == "manual":
        return label["manual"]
    return label["value"] is (wanted == "yes")


class KnowledgeLabels:
    """Step 2's answers (the original file) and the labels in force (edited copy first)."""

    def __init__(self, original: Path, edited: Path) -> None:
        self.edited = edited
        self._automatic = _read(original)
        current = _read(edited)
        self.available = self._automatic is not None
        self._payload = current if current is not None else deepcopy(self._automatic or {"threads": []})
        self.rule = LabelRule(self._automatic, current)
        self._threshold = (self._automatic or {}).get("metadata", {}).get("threshold")
        self._backed_up = False

    def state(self, threads: Iterable[dict[str, Any]], changed: set[str]) -> dict[str, Any]:
        """``{ready, reason, threshold, manual_count, needs_label_count, changed_count}`` over
        the conversations as they are now (``changed``: the ids the owner's edits made or
        changed)."""
        labels = [self.label(thread) for thread in threads]
        return {
            "ready": self.available,
            "reason": None if self.available else NOT_RUN,
            "threshold": self._threshold,
            "manual_count": sum(1 for label in labels if label and label["manual"]),
            "needs_label_count": sum(1 for label in labels if needs_label(label)),
            "changed_count": len(changed),
        }

    def label(self, thread: dict[str, Any]) -> dict[str, Any] | None:
        """The label in force, ``None`` when there is none: ``{value, manual, current, auto,
        p_knowledge, passed_heuristic}``. ``manual``: the owner set it; ``current``: it was
        given to the conversation as it is now; ``auto`` / ``p_knowledge`` /
        ``passed_heuristic``: step 2's own answer (all ``None`` when step 2 has none)."""
        thread_id = thread["thread_id"]
        record = self.rule.in_force.get(thread_id)
        if record is None:
            return None
        own = self.rule.own.get(thread_id)
        return {
            "value": bool(record.get("is_knowledge_bearing")),
            "manual": self.rule.by_hand(thread_id),
            "current": not self.rule.needs_label(thread),
            "auto": None if own is None else bool(own.get("is_knowledge_bearing")),
            "p_knowledge": None if own is None else own.get("p_knowledge"),
            "passed_heuristic": None if own is None else bool(own.get("passed_heuristic")),
        }

    def set(self, thread: dict[str, Any], value: bool | None) -> dict[str, Any] | None:
        """The owner's label for the conversation as it is now (``True`` / ``False``), or
        (``None``) take it away: back to step 2's record, or no label when step 2 has none.
        Saves the edited file; returns the new label."""
        if not self.available:
            raise EditRefused(NOT_RUN)
        thread_id = thread["thread_id"]
        own = self.rule.own.get(thread_id)
        record = deepcopy(own) if value is None else hand_record(thread, value, own)
        records = self._payload["threads"]
        at = next((i for i, r in enumerate(records) if r["thread_id"] == thread_id), None)
        if at is None:
            if record is not None:
                records.append(record)
        elif record is None:
            del records[at]
        else:
            records[at] = record
        self.rule = LabelRule(self._automatic, self._payload)
        self._save()
        return self.label(thread)

    def _save(self) -> None:
        if not self._backed_up:
            backup_file(self.edited)  # nothing to back up before the first label ever
            self._backed_up = True
        metadata = self._payload.setdefault("metadata", {})
        metadata["edited_by"] = EDITED_BY
        metadata["edited_at"] = local_now()
        write_json_file(self._payload, self.edited)
