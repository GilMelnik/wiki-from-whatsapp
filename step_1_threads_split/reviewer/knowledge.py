"""Step 2's knowledge label of each conversation, and the owner's corrections of it.

Step 2 (contract C2) answers per conversation whether it carries knowledge worth extracting:
``is_knowledge_bearing``, from the classifier's ``p_knowledge`` (null when the heuristic
rejected the conversation unasked). The owner corrects it where step 2 expects corrections:
the conversation's record in the edited file (``threads_classified_edited.json``, created from
the original by the first correction), with ``is_knowledge_bearing`` set by hand. A step-2
re-run (``utils.reconcile.write_reconciled``) keeps such a record as long as the step's own
answer for the conversation does not change, and a record equal to the original's simply
follows the step. So a label is *set by hand* exactly when the edited record differs from the
original's, and going back to the automatic answer puts the original's record back. Nothing
else of the file changes.

Use it through a ``StoreHandle`` over both files: reads via ``get()``, ``set`` inside
``mutate()``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from step_1_threads_split.reviewer.operations import EditRefused, UnknownItem
from utils.json_io import write_json_file
from utils.reconcile import backup_file
from utils.timestamps import local_now

KnowledgeFilter = Literal["all", "yes", "no", "manual"]
EDITED_BY = "threads_reviewer"
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


class KnowledgeLabels:
    """Step 2's answers (the original file) and the labels in force (edited copy first)."""

    def __init__(self, original: Path, edited: Path) -> None:
        self.edited = edited
        automatic = _read(original)
        current = _read(edited)
        self.available = automatic is not None
        self._auto = {record["thread_id"]: record for record in (automatic or {}).get("threads", [])}
        self._payload = current if current is not None else deepcopy(automatic or {"threads": []})
        self._records = {record["thread_id"]: record for record in self._payload["threads"]}
        self._threshold = (automatic or {}).get("metadata", {}).get("threshold")
        self._backed_up = False

    def state(self) -> dict[str, Any]:
        return {
            "ready": self.available,
            "reason": None if self.available else NOT_RUN,
            "threshold": self._threshold,
            "manual_count": sum(1 for record in self._payload["threads"] if self._manual(record)),
        }

    def label(self, thread_id: str) -> dict[str, Any] | None:
        """``{value, auto, p_knowledge, passed_heuristic, manual}``, or ``None`` when step 2 has
        no answer for the conversation (it did not run, or the conversation is newer)."""
        auto = self._auto.get(thread_id)
        current = self._records.get(thread_id)
        if auto is None or current is None:
            return None
        return {
            "value": bool(current.get("is_knowledge_bearing")),
            "auto": bool(auto.get("is_knowledge_bearing")),
            "p_knowledge": auto.get("p_knowledge"),
            "passed_heuristic": bool(auto.get("passed_heuristic")),
            "manual": self._manual(current),
        }

    def shown(self, thread_id: str, wanted: KnowledgeFilter) -> bool:
        """Whether the list filter ``wanted`` lets the conversation through."""
        if wanted == "all":
            return True
        label = self.label(thread_id)
        if label is None:
            return False
        if wanted == "manual":
            return label["manual"]
        return label["value"] is (wanted == "yes")

    def set(self, thread_id: str, value: bool | None) -> dict[str, Any]:
        """Set the label by hand (``True`` / ``False``), or go back to step 2's answer
        (``None``, or the value step 2 gave anyway); save; return the new label."""
        if not self.available:
            raise EditRefused(NOT_RUN)
        auto = self._auto.get(thread_id)
        if auto is None:
            raise UnknownItem(
                f"Step 2 has no answer for conversation {thread_id}: it is new since step 2 last "
                "ran (or does not exist). Run step 2 first, then label it."
            )
        record = deepcopy(auto)
        if value is not None:
            record["is_knowledge_bearing"] = value
        records = self._payload["threads"]
        if thread_id in self._records:
            records[records.index(self._records[thread_id])] = record
        else:
            records.append(record)
        self._records[thread_id] = record
        self._save()
        return self.label(thread_id)

    def _manual(self, record: dict[str, Any]) -> bool:
        auto = self._auto.get(record["thread_id"])
        return auto is not None and record != auto

    def _save(self) -> None:
        if not self._backed_up:
            backup_file(self.edited)  # nothing to back up before the first correction ever
            self._backed_up = True
        metadata = self._payload.setdefault("metadata", {})
        metadata["edited_by"] = EDITED_BY
        metadata["edited_at"] = local_now()
        write_json_file(self._payload, self.edited)
