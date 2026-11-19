"""The owner's "keep withheld" marks, as the Withheld page edits them.

A mark keeps a claim the same-complaint check would release off every page; taking it away
lets the check release it again. Use it through a ``StoreHandle``: reads via ``handle.get()``,
changes inside ``handle.mutate()`` (the file's lock); every save backs the previous file up
first (``utils.reconcile.backup_file``) and replaces it atomically. No run writes this file.
"""

from __future__ import annotations

from pathlib import Path

from step_5_same_complaint.release import load_vetoes, save_vetoes
from utils.reconcile import backup_file


class VetoTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.kept = set(load_vetoes(path))

    def mark(self, short_id: str, keep: bool) -> bool:
        """Set or take away the mark of ``short_id`` and save; ``False`` when nothing changed."""

        if (short_id in self.kept) == keep:
            return False
        if keep:
            self.kept.add(short_id)
        else:
            self.kept.discard(short_id)
        backup_file(self.path)
        save_vetoes(self.kept, self.path)
        return True
