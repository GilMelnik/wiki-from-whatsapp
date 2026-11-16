"""Trials of step 4's model stages on the current entities, scored against the seed file.

    python -m step_4_entities.match_trial --net

The names are the member names of the current entities (``entities_edited.json`` when it
exists), each with all its claims; the seed file says which names belong together. The seed
list is incomplete (the owner's words): the trials measure what the stages FIND; a model merge
the list lacks is not thereby wrong.

* ``--net``: the net over every name with the seed links hidden: how many of the seed's pairs
  it holds, in all and without each source, and how many candidate pairs it makes. Embeds the
  names (cents; cached).

Only numbers reach the console and the log. Everything with names goes to
``<data>/step_4_entities/<trial.folder>/<mode>_<stamp>.json``.
"""

from __future__ import annotations

import argparse
import itertools
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from step_4_entities.cluster import EntityClusterer, load_seed_index
from step_4_entities.collect import load_claims_for_entities
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_estimate import default_embed
from step_4_entities.match_net import MEANING_SOURCES, SPELLING_SOURCES, CandidateNet, PairKey, pair_key
from step_4_entities.mentions import DictaAnalyzer, build_or_load_analysis
from step_4_entities.normalize import normalize_name
from utils.config import ENTITIES_CONFIG
from utils.json_io import read_json_file, write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import ENTITIES_SEED_PATH, ENTITY_ANALYSIS_PATH, STEP_4, resolve, step_path
from utils.paths import ENTITIES as ENTITIES_ARTIFACT

TRIAL_CONFIG = ENTITIES_CONFIG["match"]["trial"]
STAMP = "%Y%m%d_%H%M%S"


class TrialData:
    """The current names with their claims, and the seed entries among them."""

    def __init__(self, logger: logging.Logger, seed_path: Path | None = None) -> None:
        self.logger = logger
        claims, resolved, _ = load_claims_for_entities()
        self.claims_by_id = {c["claim_id"]: c for c in claims}
        payload = read_json_file(resolve(ENTITIES_ARTIFACT))
        self.claims_of: dict[str, set[str]] = {}
        biggest: dict[str, dict[str, Any]] = {}
        for entity in payload.get("entities") or []:
            for member in entity.get("members") or []:
                name = member["name"]
                self.claims_of.setdefault(name, set()).update(member.get("claim_ids") or [])
                if (member.get("count") or 0) >= (biggest.get(name, {}).get("count") or 0):
                    biggest[name] = member
        self.members = [{**biggest[n], "claim_ids": sorted(self.claims_of[n])}
                        for n in sorted(biggest)]
        self.analysis = build_or_load_analysis(claims, DictaAnalyzer(),
                                               cache_path=ENTITY_ANALYSIS_PATH,
                                               source_path=resolved)
        self.seed_path = seed_path or ENTITIES_SEED_PATH
        norm_to_seed, _ = load_seed_index(self.seed_path)
        self.seed_of = {m["name"]: norm_to_seed.get(normalize_name(m["name"]))
                        for m in self.members}
        entries: dict[str, list[str]] = {}
        for name, seed_id in self.seed_of.items():
            if seed_id:
                entries.setdefault(seed_id, []).append(name)
        self.seed_units = [sorted(names) for names in entries.values() if len(names) > 1]
        self.seed_pairs = {pair_key(a, b) for unit in self.seed_units
                           for a, b in itertools.combinations(unit, 2)}
        self.sampler = ClaimSampler(self.claims_by_id)
        logger.info(f"Trial data: {len(self.members)} names, {len(self.seed_units)} seed "
                    f"entries with two or more names, {len(self.seed_pairs)} seed pairs")

    def net(self) -> dict[PairKey, Any]:
        """Every name's candidates, with the seed links hidden (the trial scores against them)."""

        blind = EntityClusterer(self.analysis,
                                similarity_threshold=ENTITIES_CONFIG["similarity_threshold"],
                                seed_path=self.seed_path)
        blind.norm_to_seed = {}
        names = [m["name"] for m in self.members]
        net = CandidateNet(self.members, names, blind.index(self.members), self.sampler,
                           default_embed(dry=False))
        return net.pairs()

    def claim_lists(self) -> dict[str, list[str]]:
        return {name: sorted(claims) for name, claims in self.claims_of.items()}


def _write(mode: str, payload: dict[str, Any], logger: logging.Logger) -> Path:
    path = step_path(STEP_4, TRIAL_CONFIG["folder"], f"{mode}_{datetime.now().strftime(STAMP)}.json")
    write_json_file(payload, path)
    logger.info(f"Trial {mode}: details with names written to {path}")
    return path


def net_trial(data: TrialData, logger: logging.Logger) -> dict[str, Any]:
    pairs = data.net()
    held = [p for p in data.seed_pairs if p in pairs]
    sources = (*SPELLING_SOURCES, *MEANING_SOURCES)
    without = {s: sum(1 for p in held if pairs[p].sources - {s}) for s in sources}
    alone = {s: sum(1 for p in held if pairs[p].sources == {s}) for s in sources}
    spelling_only = sum(1 for p in held if pairs[p].sources & set(SPELLING_SOURCES))
    meaning_only = sum(1 for p in held if pairs[p].sources & set(MEANING_SOURCES))
    numbers = {"names": len(data.members), "seed_pairs": len(data.seed_pairs),
               "seed_pairs_in_net": len(held), "candidate_pairs": len(pairs),
               "strong_pairs": sum(1 for c in pairs.values() if c.strong),
               "seed_pairs_held_without_source": without, "seed_pairs_only_this_source": alone,
               "seed_pairs_by_spelling_sources": spelling_only,
               "seed_pairs_by_meaning_sources": meaning_only}
    _write("net", {"numbers": numbers, "missed_seed_pairs": sorted(
        list(p) for p in data.seed_pairs - set(held))}, logger)
    return numbers


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Trials of step 4's model stages (numbers only).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--net", action="store_true", help="how many seed pairs the net holds")
    args = parser.parse_args(argv)
    logger = setup_step_logging(STEP_4)
    data = TrialData(logger)
    if args.net:
        numbers = net_trial(data, logger)
    logger.info(f"Trial numbers: {numbers}")


if __name__ == "__main__":
    main()
