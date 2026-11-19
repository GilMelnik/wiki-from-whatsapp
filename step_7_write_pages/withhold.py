"""A lone negative report about a named entity never reaches the wiki: the rule.

The owner's rule: a negative statement about anything named (a doctor, a lawyer, a clinic, an
agency, a place, a public body) is published only when at least two different members reported
it; otherwise it is withheld from every page, and never retold without the name.

``withholds`` is the rule, per claim record (C6, ``step_6_plan.claim_records``), with
``pages.withhold`` of ``config.json``: a claim is withheld when its ``stance`` is ``negative``,
it names at least one entity (its ``entity_ids`` list is not empty) and its
``supporter_count`` (distinct people who stated or backed it, counted from the private audit)
is below ``min_supporters`` (2). A negative claim that names no entity is published as before.
The record's keys are read with ``[...]``: a record of another shape raises ``KeyError``
instead of letting a lone complaint through as "names nothing".

The one way out for a claim the rule withholds is the same-complaint check
(``step_7_write_pages.same_complaint``): a model found a second person making the SAME
complaint about the same entity, and the owner did not mark the claim "keep withheld" on the
Withheld page. What is withheld NOW (the rule, minus what the check released) is
``step_7_write_pages.same_complaint_release.withheld_ids``: every reader (step 7, step 9, the
Plan page, the Withheld page) uses that one function, once per load of the records. Without the
check's answers file nothing is released.

Step 7's ``Corpus`` treats a withheld claim as placed on no page (it never reaches the writer,
another claim's ``contradicts``, the front matter or step 8's claim lines) and accounts for it
in its primary page's ``left_out`` with reason ``pages.withhold.reason``; step 9 reports a
page that still cites one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from utils.config import section

WITHHOLD_CONFIG = section("pages")["withhold"]
REASON = WITHHOLD_CONFIG["reason"]
MIN_SUPPORTERS = int(WITHHOLD_CONFIG["min_supporters"])


def withholds(claim: Mapping[str, Any]) -> bool:
    """True when a negative claim that names an entity has fewer than ``min_supporters``.

    Every key is read before deciding, so a record without one raises ``KeyError`` whatever
    its stance."""

    stance, entity_ids, supporters = claim["stance"], claim["entity_ids"], claim["supporter_count"]
    return stance == WITHHOLD_CONFIG["stance"] and bool(entity_ids) and supporters < MIN_SUPPORTERS
