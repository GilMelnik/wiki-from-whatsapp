"""``entity_matches.json``: what the model stages decided, kept next to the entities.

Written by every run that used the model stages (2-space JSON). A first run or ``--rebuild``
starts it afresh; an update adds its decisions to the ones already there. Shape::

    {"groups": [{group_id, bundle_id, label, names}],       # one real-world thing each
     "senses": [{name, targets: [{id, label}], claims: {claim_id: [target id]},
                 unclear: [claim_id], failed}],              # a name with two meanings
     "unsure": [{names: [a, b], reason, source}],            # source: judge | cut | unscreened
     "unjudged": [{name, why}],                              # why: net | judge | omitted
     "metadata": {generated_at, git_commit, models, prompt_versions, config, counts, usage,
                  runs}}

A target id is a group id (``<bundle_id>/<n>``) when the judge found the meanings, or an
entity id when an update divided the new claims of a known name between the entities holding
it. A claim about several of the things lists several targets. ``unclear`` lists the claims the answer did not place (they went to the target with the
most claims of the name); ``failed`` marks a name whose call gave no usable answer. Which pairs
the screen dropped is not kept, only how many (``counts``). The labels stay in this file only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from utils.json_io import read_json_if_present, write_json_file

SECTIONS = ("groups", "senses", "unsure", "unjudged")


class MatchRecord:
    """The decisions file, loaded once and written once per run."""

    def __init__(self, path: Path, *, fresh: bool) -> None:
        self.path = path
        stored = {} if fresh else (read_json_if_present(path) or {})
        self.data: dict[str, Any] = {key: list(stored.get(key) or []) for key in SECTIONS}
        self.metadata: dict[str, Any] = dict(stored.get("metadata") or {})
        self.changed = fresh and path.is_file()

    def add_groups(self, groups: Iterable[Mapping[str, Any]]) -> None:
        new = {g["group_id"]: dict(g) for g in groups}
        kept = [g for g in self.data["groups"] if g["group_id"] not in new]
        self.data["groups"] = kept + list(new.values())
        self.changed |= bool(new)

    def add_senses(self, entries: Iterable[Mapping[str, Any]]) -> None:
        added = [dict(entry) for entry in entries]
        self.data["senses"] += added
        self.changed |= bool(added)

    def add_unsure(self, entries: Iterable[Mapping[str, Any]]) -> None:
        seen = {(tuple(e["names"]), e["source"]) for e in self.data["unsure"]}
        for entry in entries:
            key = (tuple(entry["names"]), entry["source"])
            if key not in seen:
                seen.add(key)
                self.data["unsure"].append(dict(entry))
                self.changed = True

    def set_unjudged(self, judged: Iterable[str], unjudged: Mapping[str, str]) -> None:
        """Names judged now leave the list; names the model could not decide join it."""

        drop = set(judged) | set(unjudged)
        before = len(self.data["unjudged"])
        self.data["unjudged"] = [e for e in self.data["unjudged"] if e["name"] not in drop]
        self.data["unjudged"] += [{"name": n, "why": why} for n, why in sorted(unjudged.items())]
        self.changed |= bool(unjudged) or len(self.data["unjudged"]) != before

    def group_label(self, names: Iterable[str]) -> str:
        """The label of the stored group that shares most names with ``names`` ('' if none)."""

        wanted = set(names)
        best, overlap = "", 0
        for group in self.data["groups"]:
            score = len(wanted & set(group["names"]))
            if score > overlap or (score == overlap and score and set(group["names"]) == wanted):
                best, overlap = group.get("label") or "", score
        return best

    def save(self, metadata: Mapping[str, Any]) -> bool:
        """Write the file when this run decided anything (or started it afresh)."""

        if not self.changed:
            return False
        runs = [*(self.metadata.get("runs") or []), dict(metadata.get("counts") or {})]
        self.metadata = {**metadata, "runs": runs}
        write_json_file({**self.data, "metadata": self.metadata}, self.path)
        return True
