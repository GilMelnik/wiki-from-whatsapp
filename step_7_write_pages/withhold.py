"""A lone negative report about a named entity never reaches the wiki.

The owner's rule: a negative statement about anything named (a doctor, a lawyer, a clinic, an
agency, a place) is published only when at least two different members reported it; otherwise
it is withheld from every page, and never retold without the name. Two bad reports, no matter
what is named.

``withholds`` decides it per claim group (C6), with ``pages.withhold`` of ``config.json``: a
group is withheld when its ``stance`` is ``negative``, it names at least one entity (its
``entities`` list is not empty) and its ``supporter_count`` (distinct people who stated or
backed that same statement, counted by step 5 from the private audit; null counts as 0) is
below ``min_supporters`` (2). A negative group that names no entity is published as before.
Counts are never added up across groups: two lone complaints that say different things stay
withheld; when the owner merges them in the groups tool because they say the same, the group
reaches 2 and is released by itself.

Step 7's ``Corpus`` treats a withheld group as placed on no page (it never reaches the writer,
another group's ``contradicts``, the front matter or step 8's group lines) and accounts for it
in its primary page's ``left_out`` with reason ``pages.withhold.reason``; step 9 reports a
page that still cites one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from utils.config import section

WITHHOLD_CONFIG = section("pages")["withhold"]
REASON = WITHHOLD_CONFIG["reason"]


def withholds(group: Mapping[str, Any]) -> bool:
    """True when a negative group that names an entity has fewer than ``min_supporters``."""
    return (group.get("stance") == WITHHOLD_CONFIG["stance"]
            and bool(group.get("entities"))
            and (group.get("supporter_count") or 0) < WITHHOLD_CONFIG["min_supporters"])


def withheld_ids(groups: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(group["group_id"] for group in groups if withholds(group))
