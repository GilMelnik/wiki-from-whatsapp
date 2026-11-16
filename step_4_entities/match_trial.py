"""Trials of step 4's model stages on the current entities, scored against the seed file.

    python -m step_4_entities.match_trial --net
    python -m step_4_entities.match_trial --screen
    python -m step_4_entities.match_trial --judge BUNDLES.json [--hide-seed] [--sync]

The names are the member names of the current entities (``entities_edited.json`` when it
exists), each with all its claims; the seed file says which names belong together. The seed
list is incomplete (the owner's words): the trials measure what the stages FIND; a model merge
the list lacks is not thereby wrong.

* ``--net``: the net over every name with the seed links hidden: how many of the seed's pairs
  it holds, in all and without each source, and how many candidate pairs it makes. Embeds the
  names (cents; cached).
* ``--screen``: the screen on the seed pairs in that net that are not strong, and on a sample
  of candidate pairs whose names come from two different seed entries
  (``entities.match.trial.cross_seed_pairs``): the share kept at each bar of ``trial.bars``.
* ``--judge FILE``: the judge on the bundles a private JSON file lists (``[[name, ...], ...]``,
  kept under the data folder, never in git), with the seed entries among each bundle's names as
  fixed units, then the per-claim stage for every name the judge lists twice. ``--hide-seed``
  hides the units and counts how many of them the model rebuilds (all their names in one
  group). To test a name with two meanings, list the bundle that holds it with its seed entry:
  the counts say how many names were listed twice and how their claims were divided, and the
  result file shows the groups and the per-claim answers by name.

Only numbers reach the console and the log. Everything with names goes to
``<data>/step_4_entities/<trial.folder>/<mode>_<stamp>.json``.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import random
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from step_4_entities.cluster import EntityClusterer, load_seed_index
from step_4_entities.collect import load_claims_for_entities
from step_4_entities.match import MATCH_CONFIG, EntityMatcher
from step_4_entities.match_bundles import Bundle, bundle_id, merged_units
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_estimate import default_embed
from step_4_entities.match_judge import JUDGE_CONFIG, Judge
from step_4_entities.match_net import MEANING_SOURCES, SPELLING_SOURCES, CandidateNet, PairKey, pair_key
from step_4_entities.match_screen import SCREEN_CONFIG, PairScreen
from step_4_entities.match_senses import SenseJob, SenseSplitter
from step_4_entities.mentions import DictaAnalyzer, build_or_load_analysis
from step_4_entities.normalize import normalize_name
from utils.config import ENTITIES_CONFIG
from utils.json_io import read_json_file, write_json_file
from utils.llm_client import Classifier, LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import ENTITIES_SEED_PATH, ENTITY_ANALYSIS_PATH, STEP_4, resolve, step_path
from utils.paths import ENTITIES as ENTITIES_ARTIFACT

TRIAL_CONFIG = MATCH_CONFIG["trial"]


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
    path = step_path(STEP_4, TRIAL_CONFIG["folder"], f"{mode}_{datetime.now().strftime(TRIAL_CONFIG['stamp_format'])}.json")
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


def screen_trial(data: TrialData, logger: logging.Logger) -> dict[str, Any]:
    pairs = data.net()
    seed = sorted(p for p in data.seed_pairs if p in pairs and not pairs[p].strong)
    cross = sorted(p for p, c in pairs.items() if not c.strong
                   and data.seed_of.get(p[0]) and data.seed_of.get(p[1])
                   and data.seed_of[p[0]] != data.seed_of[p[1]])
    rng = random.Random(TRIAL_CONFIG["seed"])
    cross = sorted(rng.sample(cross, min(len(cross), TRIAL_CONFIG["cross_seed_pairs"])))
    classifier = Classifier.for_stage(SCREEN_CONFIG["stage"], logger=logger)
    screen = PairScreen(classifier, data.sampler, data.claim_lists(), logger)
    answered = screen.screen([*seed, *cross])
    prob = answered.probabilities

    def kept(found: Sequence[PairKey], bar: float) -> float:
        known = [p for p in found if p in prob]
        return round(sum(1 for p in known if prob[p] >= bar) / len(known), 4) if known else 0.0

    numbers = {"seed_pairs_screened": len(seed), "cross_seed_pairs_screened": len(cross),
               "weak_pairs_in_net": sum(1 for c in pairs.values() if not c.strong),
               "failed": len(answered.failed), "requests": answered.requests,
               "kept_share_by_bar": {str(bar): {"seed": kept(seed, bar), "cross": kept(cross, bar)}
                                     for bar in TRIAL_CONFIG["bars"]}}
    _write("screen", {"numbers": numbers,
                      "seed": [[*p, prob.get(p)] for p in seed],
                      "cross": [[*p, prob.get(p)] for p in cross]}, logger)
    return numbers


def judge_trial(data: TrialData, listed: list[list[str]], *, hide_seed: bool, batch: bool,
                logger: logging.Logger) -> dict[str, Any]:
    known = [[n for n in names if n in data.claims_of] for names in listed]
    unknown = sum(len(names) for names in listed) - sum(len(names) for names in known)
    bundles = [Bundle(bundle_id(names), sorted(names),
                      [] if hide_seed else merged_units(data.seed_units, names))
               for names in known if len(names) > 1]
    llm = LLMClient.for_stage(JUDGE_CONFIG["stage"], logger=logger,
                              max_tokens=JUDGE_CONFIG["max_output_tokens"])
    claims_of = data.claim_lists()
    answered = Judge(llm, data.sampler, claims_of, logger).judge(bundles, batch=batch)
    rebuilt = units = seed_found = seed_total = extra = 0
    shared_jobs: list[SenseJob] = []
    out_bundles = []
    for bundle in bundles:
        verdict = answered.verdicts.get(bundle.bundle_id)
        if verdict is None:
            continue
        group_sets = [set(g.names) for g in verdict.groups]
        bundle_units = merged_units(data.seed_units, bundle.names)
        units += len(bundle_units)
        rebuilt += sum(1 for u in bundle_units if any(set(u) <= g for g in group_sets))
        seeded = {p for p in data.seed_pairs if p[0] in bundle.names and p[1] in bundle.names}
        together = {pair_key(a, b) for g in group_sets for a, b in itertools.combinations(g, 2)}
        seed_total += len(seeded)
        seed_found += len(seeded & together)
        extra += len(together - data.seed_pairs)
        for name in sorted(verdict.shared()):
            groups = [g for g in verdict.groups if name in g.names]
            shared_jobs.append(SenseJob(name, tuple(claims_of[name]),
                                        tuple((g.label, tuple(g.names)) for g in groups)))
        out_bundles.append({"bundle_id": bundle.bundle_id, "names": bundle.names,
                            "units": bundle.units,
                            "groups": [{"label": g.label, "names": g.names} for g in verdict.groups],
                            "unsure": [[*p, reason] for p, reason in verdict.unsure],
                            "omitted": verdict.omitted})
    senses = SenseSplitter(llm, data.sampler, data.claims_by_id, logger).split(shared_jobs,
                                                                              batch=batch)
    divided = []
    claims_per_group: list[list[int]] = []
    for job in shared_jobs:
        picked, unclear, failed = EntityMatcher.divide(job, senses.answers.get(job.name),
                                                       [0] * len(job.groups))
        counts = Counter(i for indexes in picked.values() for i in indexes)
        claims_per_group.append([counts.get(i, 0) for i in range(len(job.groups))])
        divided.append({"name": job.name, "groups": [label for label, _ in job.groups],
                        "claims": {c: [job.groups[i][0] for i in ix] for c, ix in picked.items()},
                        "unclear": unclear, "failed": failed})
    numbers = {"bundles": len(bundles), "judged": len(answered.verdicts),
               "failed": len(answered.failed), "unknown_names": unknown,
               "hide_seed": hide_seed, "seed_units": units, "seed_units_rebuilt": rebuilt,
               "seed_pairs": seed_total, "seed_pairs_together": seed_found,
               "pairs_together_not_in_seed": extra,
               "groups": sum(len(b["groups"]) for b in out_bundles),
               "unsure_pairs": sum(len(b["unsure"]) for b in out_bundles),
               "names_with_two_meanings": len(shared_jobs),
               "claims_per_meaning": claims_per_group,
               "senses_failed": len(senses.failed),
               "chat_usage": {key: sum(int(u.get(key) or 0) for u in llm.usage)
                              for key in ("input_tokens", "output_tokens",
                                          "cache_creation_input_tokens", "cache_read_input_tokens")}}
    _write("judge", {"numbers": numbers, "bundles": out_bundles, "senses": divided}, logger)
    return numbers


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Trials of step 4's model stages (numbers only).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--net", action="store_true", help="how many seed pairs the net holds")
    mode.add_argument("--screen", action="store_true", help="the screen's share kept per bar")
    mode.add_argument("--judge", type=Path, metavar="BUNDLES_JSON",
                      help="judge the bundles a private file lists ([[name, ...], ...])")
    parser.add_argument("--hide-seed", action="store_true",
                        help="with --judge: hide the seed units and count how many come back")
    parser.add_argument("--sync", action="store_true",
                        help="with --judge: one call at a time instead of the batch API")
    args = parser.parse_args(argv)
    logger = setup_step_logging(STEP_4)
    data = TrialData(logger)
    if args.net:
        numbers = net_trial(data, logger)
    elif args.screen:
        numbers = screen_trial(data, logger)
    else:
        numbers = judge_trial(data, read_json_file(args.judge), hide_seed=args.hide_seed,
                              batch=not args.sync, logger=logger)
    logger.info(f"Trial numbers: {numbers}")


if __name__ == "__main__":
    main()
