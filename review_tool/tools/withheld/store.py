"""The owner's marks ("keep withheld", "released"), as the Withheld page edits them.

"Keep withheld" keeps a claim the same-complaint check would release off every page; "released"
publishes a claim the rule withholds, one decision per claim by the owner's hand. One claim
never carries both: setting one takes the other away. Use the table through a ``StoreHandle``:
reads via ``handle.get()``, changes inside ``handle.mutate()`` (the file's lock); every save
backs the previous file up first (``utils.reconcile.backup_file``) and replaces it atomically.
No run writes this file.
"""

from __future__ import annotations

from pathlib import Path

from step_5_same_complaint.release import Marks, load_marks, save_marks
from utils.reconcile import backup_file


class MarksTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.marks = load_marks(path)

    def keep(self, short_id: str, keep: bool) -> bool:
        """Set (taking a hand release away) or take away the "keep withheld" mark of
        ``short_id`` and save; ``False`` when nothing changed."""

        if not keep:
            return self.replace(Marks(self.marks.keep_withheld - {short_id}, self.marks.released))
        return self.replace(Marks(self.marks.keep_withheld | {short_id},
                                  self.marks.released - {short_id}))

    def replace(self, marks: Marks) -> bool:
        """Save ``marks``; ``False`` (nothing written) when they are the stored ones."""

        if marks == self.marks:
            return False
        backup_file(self.path)
        save_marks(marks, self.path)
        self.marks = marks
        return True
