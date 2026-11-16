"""Bundles: the connected names the judge sees in one call.

The nodes are the names in play; the edges are the pairs that reached the judge (strong pairs
and the pairs the screen kept). Fixed units (a seed entry; on an update, an existing entity)
are atomic: their names always land in one bundle, and joining any of them joins the unit.
Every connected set of two or more names is a bundle.

A bundle over ``entities.match.judge.max_bundle_names`` is cut deterministically at its
weakest links: the edges are taken strongest first (a strong pair weighs more than any
screened one, then by similarity or probability, then by names) and two parts are joined only
while the result stays within the cap. An edge whose names end in two different bundles is
returned as cut, so the caller records it as unsure: no pair that passed the screen is
silently dropped. A unit larger than the cap stays whole (and is logged by the caller).
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from step_4_entities.match_net import PairKey
from utils.config import ENTITIES_CONFIG

JUDGE_CONFIG = ENTITIES_CONFIG["match"]["judge"]
BUNDLE_PREFIX = "b-"


@dataclass
class Bundle:
    """One judge call's names (sorted) and its fixed units (each of two or more names)."""

    bundle_id: str
    names: list[str]
    units: list[list[str]] = field(default_factory=list)


def bundle_id(names: Iterable[str]) -> str:
    digest = hashlib.sha1("\n".join(sorted(names)).encode("utf-8")).hexdigest()
    return f"{BUNDLE_PREFIX}{digest[:JUDGE_CONFIG['bundle_hash_chars']]}"


class _Parts:
    """Union-find over names with a size per root."""

    def __init__(self, names: Iterable[str]) -> None:
        self.parent = {name: name for name in names}
        self.size = dict.fromkeys(self.parent, 1)

    def find(self, name: str) -> str:
        while self.parent[name] != name:
            self.parent[name] = self.parent[self.parent[name]]
            name = self.parent[name]
        return name

    def union(self, a: str, b: str, cap: int | None = None) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if cap is not None and self.size[ra] + self.size[rb] > cap:
            return False
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return True

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = {}
        for name in self.parent:
            out.setdefault(self.find(name), []).append(name)
        return sorted((sorted(names) for names in out.values()), key=lambda g: g[0])


def merged_units(units: Iterable[Collection[str]], names: Collection[str]) -> list[list[str]]:
    """Fixed units restricted to ``names``, overlapping ones merged, each of 2+ names."""

    known = set(names)
    parts = _Parts(known)
    for unit in units:
        present = sorted(set(unit) & known)
        for other in present[1:]:
            parts.union(present[0], other)
    return [group for group in parts.groups() if len(group) > 1]


def build_bundles(
    names: Collection[str],
    edges: Mapping[PairKey, float],
    units: Sequence[Collection[str]],
    cap: int | None = None,
) -> tuple[list[Bundle], list[PairKey]]:
    """The bundles (by first name) and the edges cut to keep them within ``cap`` (default
    ``judge.max_bundle_names``).

    ``edges`` maps each pair that reached the judge to its weight (higher = surer).
    """

    cap = JUDGE_CONFIG["max_bundle_names"] if cap is None else cap

    fixed = merged_units(units, names)
    connected = _Parts(names)
    for unit in fixed:
        for other in unit[1:]:
            connected.union(unit[0], other)
    for a, b in edges:
        connected.union(a, b)
    edges_of: dict[str, dict[PairKey, float]] = {}
    for pair, weight in edges.items():
        edges_of.setdefault(connected.find(pair[0]), {})[pair] = weight
    units_of: dict[str, list[list[str]]] = {}
    for unit in fixed:
        units_of.setdefault(connected.find(unit[0]), []).append(unit)
    bundles: list[Bundle] = []
    cut: list[PairKey] = []
    for component in connected.groups():
        if len(component) < 2:
            continue
        root = connected.find(component[0])
        inside = edges_of.get(root, {})
        comp_units = units_of.get(root, [])
        placed: dict[str, str] = {}
        for part in _cut(component, inside, comp_units, cap):
            if len(part) > 1:
                keep = set(part)
                bundle = Bundle(bundle_id(part), part, [u for u in comp_units if u[0] in keep])
                bundles.append(bundle)
                placed.update(dict.fromkeys(part, bundle.bundle_id))
        cut += sorted(p for p in inside if placed.get(p[0]) is None
                      or placed.get(p[0]) != placed.get(p[1]))
    return bundles, cut


def _cut(component: list[str], edges: Mapping[PairKey, float], units: list[list[str]],
         cap: int) -> list[list[str]]:
    """The component itself when it fits, else its parts joined strongest edge first."""

    if len(component) <= cap:
        return [component]
    parts = _Parts(component)
    for unit in units:
        for other in unit[1:]:
            parts.union(unit[0], other)
    for pair in sorted(edges, key=lambda p: (-edges[p], p)):
        parts.union(pair[0], pair[1], cap)
    return parts.groups()
