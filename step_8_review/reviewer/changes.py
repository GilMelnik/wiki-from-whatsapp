"""What an applied suggestion changed, from its ``apply_result`` and the backups it names.

- structural: the plan change as the plan operation reported it (created, removed pages and
  labels, redirects, pages whose groups are classified again) and the steps that must follow;
- mistake / contradiction: per corrected page, the sentences before and after. "Before" is the
  pages' edited file as the apply found it (the backup the result names; without one, the
  step's original, from which the edited file was created). "After" is the edited file as the
  apply left it: every later write of that file backs it up first, so it is the first backup
  taken after the apply, or the file itself while nothing has written it since;
- contradiction: also the group pairs saved as contradiction links.

A wrong apply is repaired from the backup the result names; there is no undo here.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from step_8_review.apply import sentences
from step_8_review.proposals import APPLIED, CONTRADICTION, STRUCTURAL, plain_text
from step_8_review.reviewer.evidence import WikiView
from utils.json_io import read_json_if_present
from utils.paths import Artifact
from utils.reconcile import BACKUP_STAMP

PLAN_FIELDS = ("created", "removed_pages", "removed_labels", "redirects", "reassign")
# The steps a plan change needs before the pages show it, by the artifact they produce.
FOLLOW_UP = ("assignments", "pages")


def _backup_key(path: Path, pattern: re.Pattern[str]) -> tuple[str, int] | None:
    match = pattern.match(path.name)
    return (match[1], int(match[2] or 0)) if match else None


def snapshot_after(path: Path, backup: Path | None, folder: Path, applied_at: str) -> Path:
    """The file as one write left it: the first backup of ``path`` in ``folder`` taken after
    that write, or ``path`` itself when none was. The write is the one whose backup is
    ``backup``, or (no backup: ``path`` did not exist before) the one at ``applied_at``."""
    pattern = re.compile(rf"^{re.escape(path.stem)}_(\d{{8}}_\d{{6}})(?:_(\d+))?{re.escape(path.suffix)}$")
    stamped = sorted((key, candidate) for candidate in folder.glob(f"{path.stem}_*{path.suffix}")
                     if (key := _backup_key(candidate, pattern)) is not None)
    if backup is not None:
        after = _backup_key(backup, pattern) or ("", 0)
        later = [candidate for key, candidate in stamped if key > after]
    else:
        moment = datetime.fromisoformat(applied_at.replace("Z", "+00:00")).astimezone()
        floor = moment.strftime(BACKUP_STAMP)  # backups are named in local time
        later = [candidate for key, candidate in stamped if key[0] >= floor]
    return later[0] if later else path


def sentence_changes(before: str, after: str) -> list[dict[str, list[str]]]:
    """The runs of sentences that differ, each as ``{before, after}`` (as a reader reads them)."""
    old, new = sentences(before), sentences(after)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    return [{"before": [plain_text(s) for s in old[i1:i2]], "after": [plain_text(s) for s in new[j1:j2]]}
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


class AppliedChanges:
    """Reads what applied suggestions changed in one pages artifact."""

    def __init__(self, pages_artifact: Artifact, backups_dir: Path) -> None:
        self.pages_artifact = pages_artifact
        self.backups_dir = backups_dir

    def of(self, suggestion: Mapping[str, Any], view: WikiView | None) -> dict[str, Any] | None:
        """What ``suggestion`` changed (``None`` while it is not applied). ``view`` names the
        linked groups of a contradiction (``None``: ids only)."""
        result = suggestion.get("apply_result")
        if suggestion["status"] != APPLIED or not result:
            return None
        common = {"message": result.get("message", ""), "file": result.get("file"),
                  "backup": result.get("backup"), "applied_at": suggestion.get("applied_at")}
        if suggestion["kind"] in STRUCTURAL:
            return {**common, "plan": {key: result.get(key) for key in PLAN_FIELDS},
                    "follow_up": list(FOLLOW_UP)}
        changes = {**common, "pages": self._pages(result, suggestion.get("applied_at") or "")}
        if suggestion["kind"] == CONTRADICTION:
            changes["links"] = [
                {"a": view.group(a) if view else {"group_id": a},
                 "b": view.group(b) if view else {"group_id": b}}
                for a, b in result.get("links") or []]
            changes["groups_file"] = result.get("groups_file")
            changes["groups_backup"] = result.get("groups_backup")
        return changes

    def _pages(self, result: Mapping[str, Any], applied_at: str) -> list[dict[str, Any]]:
        backup = Path(result["backup"]) if result.get("backup") else None
        before_path = backup or self.pages_artifact.original
        after_path = snapshot_after(self.pages_artifact.edited, backup, self.backups_dir, applied_at)
        before = (read_json_if_present(before_path) or {}).get("pages") or {}
        after = (read_json_if_present(after_path) or {}).get("pages") or {}
        pages = []
        for page_id, info in (result.get("pages") or {}).items():
            changed = info.get("changed", True)
            old, new = before.get(page_id), after.get(page_id)
            entry = {"page_id": page_id, "changed": changed,
                     "kept_sentence_share": info.get("kept_sentence_share"),
                     "title": (new or old or {}).get("front_matter", {}).get("title", page_id),
                     "rewritten_since": after_path != self.pages_artifact.edited}
            if changed and old and new:
                entry["sentences"] = sentence_changes(old["body_md"], new["body_md"])
            pages.append(entry)
        return pages
