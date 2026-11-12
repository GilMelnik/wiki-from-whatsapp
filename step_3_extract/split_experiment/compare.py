"""The split experiment's numbers: what each variant cost and found, and how its claims match
the reference variant B (the owner's conversations).

Matching needs no model call. Two claims of the same window match when the messages they rest
on (``supporting_message_ids``) overlap: shared messages / the smaller claim's messages
(the overlap coefficient) at least ``match_min_overlap``. Matching is one to one: the pairs
with the highest overlap (then the highest Jaccard index) are taken first, and a claim takes
part in one pair at most. Against B a variant then has ``found`` (B's claims it matched),
``missed`` (B's claims it did not) and ``extra`` (its claims that matched none of B's). It is a
proxy: two claims about different things in the same message match too; the owner's page shows
the texts side by side.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_3_extract.prompt import STANCES
from step_3_extract.split_experiment.units import EXPERIMENT_CONFIG, Unit
from step_3_extract.split_experiment.usage import TOKEN_KEYS

REFERENCE = "B"


@dataclass
class VariantRun:
    """One variant's outcome: its units, the claims, failures, and reported usage."""

    variant: str
    units: list[Unit]
    claims: list[dict[str, Any]] = field(default_factory=list)
    no_answer: list[str] = field(default_factory=list)  # units the model gave no answer for
    schema_failures: list[str] = field(default_factory=list)  # answers of the wrong shape
    ungrounded: int = 0
    dropped: int = 0
    background_citing: int = 0
    usage: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_KEYS, 0))
    usd: float = 0.0
    fresh_calls: int = 0  # calls the API answered in this run
    cached_calls: int = 0  # answered by the disk cache, usage kept from the run that paid
    unpriced_calls: int = 0  # answered by the disk cache with no usage on record
    estimate: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"variant": self.variant, "units": [u.to_json() for u in self.units],
                "claims": self.claims, "no_answer": self.no_answer,
                "schema_failures": self.schema_failures, "ungrounded": self.ungrounded,
                "dropped": self.dropped, "background_citing": self.background_citing,
                "usage": self.usage, "usd": round(self.usd, 4), "fresh_calls": self.fresh_calls,
                "cached_calls": self.cached_calls, "unpriced_calls": self.unpriced_calls,
                "estimate": self.estimate}


def overlap(a: Sequence[str], b: Sequence[str]) -> tuple[float, float]:
    """(overlap coefficient, Jaccard index) of two claims' supporting messages."""
    first, second = set(a), set(b)
    if not first or not second:
        return 0.0, 0.0
    shared = len(first & second)
    return shared / min(len(first), len(second)), shared / len(first | second)


def match(reference: Sequence[dict[str, Any]], other: Sequence[dict[str, Any]],
          min_overlap: float = EXPERIMENT_CONFIG["match_min_overlap"]) -> dict[str, str]:
    """``other`` claim id -> the ``reference`` claim id it matches (see the module docstring)."""
    pairs = []
    for r, ref in enumerate(reference):
        for o, claim in enumerate(other):
            if ref["window"] != claim["window"]:
                continue
            coefficient, jaccard = overlap(ref["supporting_message_ids"],
                                           claim["supporting_message_ids"])
            if coefficient >= min_overlap:
                pairs.append((-coefficient, -jaccard, r, o))
    taken_ref: set[int] = set()
    found: dict[str, str] = {}
    for _, _, r, o in sorted(pairs):
        claim_id = other[o]["claim_id"]
        if r in taken_ref or claim_id in found:
            continue
        taken_ref.add(r)
        found[claim_id] = reference[r]["claim_id"]
    return found


def against_reference(runs: Mapping[str, VariantRun]) -> dict[str, dict[str, str]]:
    """Per variant other than B, its claim id -> the B claim id it matches."""
    if REFERENCE not in runs:
        return {}
    reference = runs[REFERENCE].claims
    return {variant: match(reference, run.claims) for variant, run in runs.items()
            if variant != REFERENCE}


ROWS: tuple[tuple[str, str], ...] = (
    ("units", "units (= calls)"),
    ("fresh_calls", "calls the API answered in this run"),
    ("cached_calls", "calls answered by the disk cache (cost kept from the paid run)"),
    ("unpriced_calls", "calls answered by the disk cache with no cost on record"),
    ("input_tokens", "input tokens, fresh (API)"),
    ("cache_creation_input_tokens", "input tokens written to the prompt cache (API)"),
    ("cache_read_input_tokens", "input tokens read from the prompt cache (API)"),
    ("output_tokens", "output tokens (API)"),
    ("usd", "cost, USD (from the API's usage)"),
    ("estimated_input_tokens", "estimated input tokens (by characters)"),
    ("estimated_output_tokens", "estimated output tokens"),
    ("estimated_usd", "estimated cost, USD"),
    ("claims", "claims"),
    ("claims_per_100", "claims per 100 messages"),
    *((f"stance_{stance}", f"stance: {stance}") for stance in STANCES),
    ("two_supporters", "claims with 2+ supporters"),
    ("dispute_links", "dispute links (counters)"),
    ("unclaimed_messages", "messages no claim rests on"),
    ("background_citing", "claims that cited a background message (D; should be 0)"),
    ("multi_conversation", "claims resting on 2+ conversations (E)"),
    ("no_answer", "calls without an answer"),
    ("schema_failures", "answers that failed the schema"),
    ("dropped", "claims dropped without supporting messages"),
    ("found", "B's claims also found (matched)"),
    ("missed", "B's claims missed"),
    ("extra", "claims B does not have"),
)


def metrics(run: VariantRun, set_ids: set[str],
            matched: Mapping[str, str] | None, reference_size: int) -> dict[str, Any]:
    """The table column of one variant (counts only, no text)."""
    claims = run.claims
    stances = Counter(claim["stance"] for claim in claims)
    rested = {wa_id for claim in claims for wa_id in claim["supporting_message_ids"]}
    column: dict[str, Any] = {
        "units": len(run.units), "fresh_calls": run.fresh_calls,
        "cached_calls": run.cached_calls, "unpriced_calls": run.unpriced_calls,
        **run.usage, "usd": round(run.usd, 4),
        "estimated_input_tokens": run.estimate.get("input_tokens"),
        "estimated_output_tokens": run.estimate.get("output_tokens"),
        "estimated_usd": run.estimate.get("usd"),
        "claims": len(claims),
        "claims_per_100": round(100 * len(claims) / len(set_ids), 1) if set_ids else 0.0,
        **{f"stance_{stance}": stances.get(stance, 0) for stance in STANCES},
        "two_supporters": sum(1 for claim in claims if claim["supporter_count"] >= 2),
        "dispute_links": sum(len(claim["counters"]) for claim in claims) // 2,
        "unclaimed_messages": len(set_ids - rested),
        "background_citing": run.background_citing,
        "multi_conversation": sum(1 for claim in claims if claim["conversations"] > 1),
        "no_answer": len(run.no_answer), "schema_failures": len(run.schema_failures),
        "dropped": run.ungrounded + run.dropped,
    }
    if matched is not None:
        column.update(found=len(matched), missed=reference_size - len(matched),
                      extra=len(claims) - len(matched))
    return column


def table(runs: Mapping[str, VariantRun], set_ids: set[str],
          matches: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, Any]]:
    """variant -> its column (``ROWS``); the match rows for the variants other than B, when
    B ran."""
    reference_size = len(runs[REFERENCE].claims) if REFERENCE in runs else 0
    return {variant: metrics(run, set_ids, matches.get(variant), reference_size)
            for variant, run in runs.items()}


def table_lines(columns: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The table as aligned text lines for the log."""
    variants = list(columns)
    lines = [f"{'':62}" + "".join(f"{v:>10}" for v in variants)]
    for key, label in ROWS:
        if any(key in columns[v] for v in variants):
            cells = "".join(f"{shown(columns[v].get(key)):>10}" for v in variants)
            lines.append(f"{label:62}{cells}")
    return lines


def shown(value: Any) -> str:
    """A table cell as text ("-" for none)."""
    return "-" if value is None else str(value)
