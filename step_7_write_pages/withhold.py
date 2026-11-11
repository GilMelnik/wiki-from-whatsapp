"""A lone negative report about a named individual never reaches the wiki.

The owner's rule: a negative statement about a named individual (a doctor, a lawyer, a
consultant known by their own name) is published only when at least two different members
reported it; otherwise it is withheld from every page, and never retold without the name.

``WithholdRule`` decides it per claim group (C6), with ``pages.withhold`` of ``config.json``:
a group is withheld when its ``stance`` is ``negative``, at least one of its ``entities`` is a
person, and its ``supporter_count`` (distinct people who stated or backed that same statement,
counted by step 5 from the private audit) is below ``min_supporters`` (2). Counts are never
added up across groups: two lone complaints that say different things stay withheld; when the
owner merges them in the groups tool because they say the same, the group reaches 2 and is
released by itself.

When in doubt, withhold: an entity counts as a person unless step 4 (or the owner) gave it
another known kind (``step_4_entities.kinds``); an entity id the entities file does not hold
counts as a person too. Businesses are not covered (the owner limited the rule to
individuals).

Step 7's ``Corpus`` treats a withheld group as placed on no page (it never reaches the writer,
another group's ``contradicts``, the front matter or step 8's group lines) and accounts for it
in its primary page's ``left_out`` with reason ``pages.withhold.reason``; step 9 reports a
page that still cites one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from step_4_entities.kinds import KINDS
from utils.config import section

WITHHOLD_CONFIG = section("pages")["withhold"]
REASON = WITHHOLD_CONFIG["reason"]


class WithholdRule:
    """The rule over one entities file: ``kinds`` maps entity id -> kind (``None`` if none)."""

    def __init__(self, kinds: Mapping[str, str | None]) -> None:
        self.kinds = dict(kinds)

    @classmethod
    def load(cls, entities_path: Path) -> WithholdRule:
        """The rule with the kinds of the entities file (C14 edited-if-present, step 4)."""
        if not entities_path.is_file():
            raise FileNotFoundError(f"the rule on negative statements about named individuals "
                                    f"needs the entities at {entities_path}; run step 4 first")
        try:
            payload = json.loads(entities_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{entities_path} is not valid JSON: {exc}") from exc
        return cls({entity["entity_id"]: entity.get("kind")
                    for entity in payload.get("entities") or []})

    def is_individual(self, entity_id: str) -> bool:
        """A person, or an entity whose kind is unknown (when in doubt, a person)."""
        kind = self.kinds.get(entity_id)
        return kind not in KINDS or kind in WITHHOLD_CONFIG["kinds"]

    def withholds(self, group: Mapping[str, Any]) -> bool:
        return (group.get("stance") == WITHHOLD_CONFIG["stance"]
                and (group.get("supporter_count") or 0) < WITHHOLD_CONFIG["min_supporters"]
                and any(self.is_individual(entity_id) for entity_id in group.get("entities") or []))

    def withheld_ids(self, groups: Iterable[Mapping[str, Any]]) -> frozenset[str]:
        return frozenset(group["group_id"] for group in groups if self.withholds(group))
