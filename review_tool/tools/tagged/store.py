"""The owner's table of tagged members (``step_3_extract.tagged``), as the Tagged members page
edits it.

Per tagged member the owner says "provider" with the public name step 3 shows for their tag,
or "not a provider", or takes the decision away (the member is undecided again). Use it through
a ``StoreHandle``: reads via ``handle.get()``, changes inside ``handle.mutate()`` (the table's
lock); every save backs the previous table up first (``utils.reconcile.backup_file``) and
replaces it atomically.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from review_tool.fs import iso_utc
from step_3_extract.tagged import load_table
from utils.json_io import write_json_file
from utils.reconcile import backup_file


class DecisionConflict(ValueError):
    """The requested change does not fit the member's current decision."""


class TaggedTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.members = load_table(path)

    def decide(self, member: str, provider: bool, public_name: str | None) -> dict[str, Any]:
        """Record "provider" (with its public name) or "not a provider" and save."""
        name = " ".join((public_name or "").split())
        if provider and not name:
            raise DecisionConflict(
                "A provider needs the public name step 3 shows for their tag. Type it or pick a "
                "suggested name."
            )
        decided_at = iso_utc(time.time())
        entry: dict[str, Any] = (
            {"provider": True, "public_name": name, "decided_at": decided_at}
            if provider
            else {"provider": False, "decided_at": decided_at}
        )
        self.members[member] = entry
        self._save()
        return entry

    def clear(self, member: str) -> None:
        """Take the decision away: the member is undecided again."""
        if member not in self.members:
            raise DecisionConflict("This member has no decision to clear.")
        del self.members[member]
        self._save()

    def _save(self) -> None:
        backup_file(self.path)
        write_json_file({"members": dict(sorted(self.members.items()))}, self.path)
