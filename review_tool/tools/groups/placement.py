"""The page a claim group would be written on, for the groups tool: its primary page in the
assignments (C8, step 6b), read through today's plan (C7), so a page merged away since the
assignment run names the page that took it over.

Both files are optional here: before step 6 or 6b has run (or when a file cannot be read) no
group has a page, and the tool says so instead of failing. Nothing here writes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from review_tool.tools.plan.views import redirect_map

logger = logging.getLogger(__name__)
# What a plan or assignments file that is not readable as C7 / C8 raises while it is read.
_UNREADABLE = (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError)


class Placement:
    """``group_id -> {page_id, title_he}`` of every group the assignments put on a page."""

    def __init__(self, plan_path: Path | None, assignments_path: Path | None) -> None:
        self.titles: dict[str, str] = {}
        self.primary: dict[str, str] = {}
        if plan_path is None or assignments_path is None:
            return
        if not plan_path.is_file() or not assignments_path.is_file():
            return
        try:
            self._load(plan_path, assignments_path)
        except _UNREADABLE as exc:
            logger.warning(f"Pages of the groups unknown: {plan_path} or {assignments_path} "
                           f"cannot be read as a plan and its assignments: {exc!r}")
            self.titles, self.primary = {}, {}

    def _load(self, plan_path: Path, assignments_path: Path) -> None:
        plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
        records = json.loads(assignments_path.read_text(encoding="utf-8"))["assignments"]
        self.titles = {page_id: page["title_he"] for page_id, page in plan["pages"].items()}
        redirects = redirect_map(plan)
        for group_id, record in records.items():
            pages = record.get("pages") or []
            page_id = redirects.get(pages[0], pages[0]) if pages else None
            if page_id in self.titles:
                self.primary[group_id] = page_id

    def page_of(self, group_id: str) -> dict[str, str] | None:
        page_id = self.primary.get(group_id)
        return None if page_id is None else {"page_id": page_id, "title_he": self.titles[page_id]}
