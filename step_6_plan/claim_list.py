"""The claims a planning run works on, and whether planning may start.

The planner never sees a claim withheld from the wiki now (``step_5_same_complaint.release.
withheld_ids``: the withholding rule, minus what the same-complaint check released, with the
owner's "keep withheld" marks, minus what the owner released by hand). What is withheld is settled only once the check has answered
every entity list it could release a claim from, so a run that would make a planning call
refuses while a list still waits for it (the check's own state ``WAITING``, the lists a run
of ``step_5_same_complaint.run`` would ask about). The assignment (step 7) still places every
claim, withheld or not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from step_5_same_complaint.claim_records import ClaimSources, Record
from step_5_same_complaint.release import WAITING, Check, withheld_ids


class PlanningRefused(RuntimeError):
    """A planning call would run before the same-complaint check answered every list."""


@dataclass(frozen=True)
class PlanningList:
    """The claim records without the withheld ones (in the order of the claims file), how
    many were left out, and how many entity lists still wait for the same-complaint check."""

    claims: list[Record]
    withheld_left_out: int
    waiting_lists: int

    @classmethod
    def load(cls, records: Sequence[Record], sources: ClaimSources) -> PlanningList:
        """The list over ``records``, with the audit, answers and marks of ``sources``."""

        withheld = withheld_ids(records, sources)
        states = Check.load(records, sources).states.values()
        return cls([record for record in records if record["short_id"] not in withheld],
                   len(withheld), sum(1 for state in states if state == WAITING))

    def waiting(self) -> str:
        """``"N entity lists wait"`` (``"1 entity list waits"``)."""

        if self.waiting_lists == 1:
            return "1 entity list waits"
        return f"{self.waiting_lists} entity lists wait"

    def refusal(self) -> str | None:
        """Why planning may not start now; ``None`` when it may."""

        if not self.waiting_lists:
            return None
        return (
            f"Planning refused before any model call: {self.waiting()} for the same-complaint "
            "check, so which claims are withheld from the wiki is not settled yet. Run python "
            "-m step_5_same_complaint.run, review the Withheld page (python -m review_tool "
            "--tool withheld), then plan."
        )
