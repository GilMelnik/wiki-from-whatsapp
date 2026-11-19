"""A page's points: the list the writer makes before the article, and its accounting.

Before the body, the writer lists the page's points: each is ONE thing the page says,
``{kind, statement, claims}`` (``kind`` from ``pages.points.kinds``, ``statement`` one short
Hebrew sentence, ``claims`` the short ids of every ``main`` claim behind it), and the main
claims it leaves out, ``{claim, reason}`` (``reason`` from ``pages.points.left_out_reasons``). Both are stored with
the page (C9 ``points`` / ``left_out``) and are where an update starts from.

Accounting (``account``): every main claim of the page is in exactly one point, or left out
once. ``mention`` claims are outside it (a brief mention with a link, as before). The body is
read as statements (``Statement``: a sentence that carries at least one ``[^g-…]`` mark; the
lead is the first paragraph, before any heading): every point must be carried by a statement that
cites one of its claims (``citing``), and ``point_report`` measures what the owner cares
about: how many points of which kind, what was left out and why, how many statements and
marks, points told more than once and points no statement carries.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from step_7_write_pages.inputs import PAGES_CONFIG

POINTS_CONFIG = PAGES_CONFIG["points"]
KINDS = tuple(POINTS_CONFIG["kinds"])
REASONS = tuple(POINTS_CONFIG["left_out_reasons"])
TOLD_CONFIG = PAGES_CONFIG["max_statements_per_point"]
Point = dict[str, Any]
LeftOut = dict[str, str]


@dataclass(frozen=True)
class Statement:
    """One sentence of a body that cites claims: its text, the distinct ids it cites and
    whether it is in the lead (the first paragraph, before any heading)."""

    text: str
    marks: tuple[str, ...]
    lead: bool


@dataclass(frozen=True)
class Accounting:
    """What is wrong with a points list against the page's main claims."""

    missing: tuple[str, ...]    # main claims in no point and not left out
    repeated: tuple[str, ...]   # listed more than once (in points and left_out together)
    foreign: tuple[str, ...]    # listed, but not a main claim of the page
    empty: int                  # points without a claim
    kinds: tuple[str, ...]      # unknown kinds
    reasons: tuple[str, ...]    # unknown left-out reasons

    def problems(self) -> list[str]:
        found = []
        if self.missing:
            found.append("these main claims are in no point and not in left_out; put each in the "
                         "point it supports (or make it a point of its own) or leave it out with "
                         f"a reason: {', '.join(self.missing)}")
        if self.repeated:
            found.append("these claims are listed more than once (each main claim belongs to "
                         f"exactly one point, or to left_out): {', '.join(self.repeated)}")
        if self.foreign:
            found.append("points or left_out list ids that are not main claims of this page "
                         f"(mention claims stay out of the points): {', '.join(self.foreign)}")
        if self.empty:
            found.append(f"{self.empty} point(s) have no claims; every point lists its claims")
        if self.kinds:
            found.append(f"unknown point kinds {', '.join(self.kinds)}; use one of {', '.join(KINDS)}")
        if self.reasons:
            found.append(f"unknown left_out reasons {', '.join(self.reasons)}; use one of "
                         f"{', '.join(REASONS)}")
        return found


def account(points: Sequence[Point], left_out: Sequence[LeftOut],
            main_ids: Collection[str]) -> Accounting:
    """Check that every main claim is listed exactly once (see the module docstring)."""
    listed = Counter([sid for point in points for sid in point["claims"]]
                     + [entry["claim"] for entry in left_out])
    mains = set(main_ids)
    return Accounting(
        missing=tuple(sid for sid in main_ids if sid not in listed),
        repeated=tuple(sid for sid, count in listed.items() if count > 1 and sid in mains),
        foreign=tuple(sid for sid in listed if sid not in mains),
        empty=sum(1 for point in points if not point["claims"]),
        kinds=tuple(dict.fromkeys(p["kind"] for p in points if p["kind"] not in KINDS)),
        reasons=tuple(dict.fromkeys(e["reason"] for e in left_out if e["reason"] not in REASONS)),
    )


@dataclass(frozen=True)
class Tidied:
    """A points list with only the page's main claims, each listed once."""

    points: list[Point]
    left_out: list[LeftOut]
    emptied: list[Point]        # points that had claims and lost them all
    dropped: list[str]          # ids taken out (not main claims, or listed again)


def tidy(points: Sequence[Point], left_out: Sequence[LeftOut],
         main_ids: Collection[str]) -> Tidied:
    """Keep only main claims of the page, each at its first listing (points before
    ``left_out``); a point left without claims is dropped. Used to repair an answer and to
    carry stored points into an update (claims that left the page leave their point)."""
    mains, seen = set(main_ids), set()
    kept, emptied, dropped = [], [], []
    for point in points:
        claims = [sid for sid in dict.fromkeys(point["claims"]) if sid in mains and sid not in seen]
        dropped += [sid for sid in point["claims"] if sid not in claims]
        seen.update(claims)
        if claims:
            kept.append({**point, "claims": claims})
        elif point["claims"]:
            emptied.append(point)
    left = []
    for entry in left_out:
        if entry["claim"] in mains and entry["claim"] not in seen:
            seen.add(entry["claim"])
            left.append(dict(entry))
        else:
            dropped.append(entry["claim"])
    return Tidied(kept, left, emptied, dropped)


def citing(point: Point, statements: Sequence[Statement]) -> list[Statement]:
    """The statements that cite one of ``point``'s claims."""
    claims = set(point["claims"])
    return [statement for statement in statements if claims.intersection(statement.marks)]


def told_cap(kind: str) -> int:
    """Most statements after the lead a point of ``kind`` may be told in: a dispute needs a
    sentence per side and a change a sentence per stage (``pages.max_statements_per_point``)."""
    return TOLD_CONFIG["by_kind"].get(kind, TOLD_CONFIG["default"])


def retold(points: Sequence[Point], statements: Sequence[Statement],
           frozen: Collection[str] = ()) -> list[tuple[Point, int]]:
    """Points told in more than ``told_cap`` of their kind statements after the lead, with the
    count; a point whose statements are all in ``frozen`` (kept text of an update) is not
    counted."""
    found = []
    for point in points:
        told = [s for s in citing(point, statements) if not s.lead]
        if len(told) > told_cap(point["kind"]) and any(s.text not in frozen for s in told):
            found.append((point, len(told)))
    return found


def point_report(points: Sequence[Point], left_out: Sequence[LeftOut],
                 statements: Sequence[Statement], main_ids: Collection[str]) -> dict[str, Any]:
    """The per-page numbers stored in the run report (see the module docstring)."""
    mains = len(main_ids)
    share = len(left_out) / mains if mains else 0.0
    return {
        "points": len(points),
        "points_by_kind": dict(Counter(point["kind"] for point in points)),
        "left_out": len(left_out),
        "left_out_share": round(share, 3),
        "left_out_by_reason": dict(Counter(entry["reason"] for entry in left_out)),
        "unaccounted": list(account(points, left_out, main_ids).missing),
        "statements": len(statements),
        "marks": sum(len(statement.marks) for statement in statements),
        "retold": len(retold(points, statements)),
        "uncarried": sum(1 for point in points if not citing(point, statements)),
    }
