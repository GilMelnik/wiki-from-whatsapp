"""A lone negative report about a named entity never reaches the wiki.

The owner's rule: a negative statement about anything named (a doctor, a lawyer, a clinic, an
agency, a place) is published only when at least two different members reported it; otherwise
it is withheld from every page, and never retold without the name. Two bad reports, no matter
what is named.

``withholds`` decides it per claim record (C6, ``step_6_plan.claim_records``), with
``pages.withhold`` of ``config.json``: a claim is withheld when its ``stance`` is ``negative``,
it names at least one entity (its ``entity_ids`` list is not empty) and its
``supporter_count`` (distinct people who stated or backed it, counted from the private audit)
is below ``min_supporters`` (2). A negative claim that names no entity is published as before.
Counts are never added up across claims: two lone complaints stay withheld, even when they say
the same thing. The record's keys are read with ``[...]``: a record of another shape raises
``KeyError`` instead of letting a lone complaint through as "names nothing".

Step 7's ``Corpus`` treats a withheld claim as placed on no page (it never reaches the writer,
another claim's ``contradicts``, the front matter or step 8's claim lines) and accounts for it
in its primary page's ``left_out`` with reason ``pages.withhold.reason``; step 9 reports a
page that still cites one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from utils.config import section

WITHHOLD_CONFIG = section("pages")["withhold"]
REASON = WITHHOLD_CONFIG["reason"]


def withholds(claim: Mapping[str, Any]) -> bool:
    """True when a negative claim that names an entity has fewer than ``min_supporters``.

    Every key is read before deciding, so a record without one raises ``KeyError`` whatever
    its stance."""

    stance, entity_ids, supporters = claim["stance"], claim["entity_ids"], claim["supporter_count"]
    return (stance == WITHHOLD_CONFIG["stance"] and bool(entity_ids)
            and supporters < WITHHOLD_CONFIG["min_supporters"])


def withheld_ids(claims: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The short ids of the withheld claims."""

    return frozenset(claim["short_id"] for claim in claims if withholds(claim))
