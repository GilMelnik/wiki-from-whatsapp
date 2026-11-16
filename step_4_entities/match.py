"""The model matching of step 4: which new names are the same real-world thing.

``EntityMatcher.match`` runs the four stages for the names ``EntityUpdate._assign`` receives as
new (every name on a first run or ``--rebuild``; the unseen names on an update, with the
existing names as partners):

1. the net of candidate pairs (``match_net``: spelling and nearest neighbours by meaning);
2. the screen (``match_screen``: Jev drops clear non-matches; strong spelling pairs skip it);
3. the judge (``match_bundles`` + ``match_judge``: one Opus call per bundle of connected
   names, the seed entries and, on an update, the existing entities as fixed units);
4. the names with two meanings (``match_senses``: per claim, which group is meant).

The result (``MatchResult``) says, for every new name, the groups it belongs to, how a name
with two meanings divides its claims, the pairs to flag, and the names the model could not
decide (they keep the spelling rules; see ``match_apply``). Every decision is added to
``entity_matches.json`` (``match_record``). No stage has a fallback model: a failed call leaves
its names undecided, never silently.

``dry=True`` (the estimate) asks nothing: the embeddings, screen answers and judge answers
already in the disk caches are used, the rest is counted in ``tally`` (see ``match_estimate``).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from step_4_entities.cluster import EntityClusterer
from step_4_entities.collect import ClaimCorpus
from step_4_entities.match_bundles import JUDGE_CONFIG, Bundle, build_bundles
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_estimate import EmbeddingsMissing, Tally, default_embed
from step_4_entities.match_judge import Judge, Verdict
from step_4_entities.match_net import (
    MEANING_SOURCES,
    NET_CONFIG,
    SPELLING_SOURCES,
    Candidate,
    CandidateNet,
    EmbedFunction,
    PairKey,
)
from step_4_entities.match_record import MatchRecord
from step_4_entities.match_screen import SCREEN_CONFIG, PairScreen
from step_4_entities.match_senses import SenseJob, SenseSplitter
from step_4_entities.normalize import normalize_name
from utils.config import ENTITIES_CONFIG
from utils.llm_client import Classifier, LLMClient

MATCH_CONFIG = ENTITIES_CONFIG["match"]
# Strong pairs weigh more than any screen probability when an oversized bundle is cut.
STRONG_WEIGHT = 2.0
SOURCE_JUDGE = "judge"
SOURCE_CUT = "cut"
SOURCE_UNSCREENED = "unscreened"
WHY_NET = "net"
WHY_JUDGE = "judge"
WHY_OMITTED = "omitted"

Existing = Sequence[tuple[dict[str, Any], dict[str, Any]]]


@dataclass
class MatchResult:
    """What the model stages decided for the new names of one run."""

    groups: list[dict[str, Any]] = field(default_factory=list)  # group_id, label, names
    senses: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # name -> claim -> groups
    unsure: list[dict[str, Any]] = field(default_factory=list)  # names, reason, source
    judged: set[str] = field(default_factory=set)
    unjudged: dict[str, str] = field(default_factory=dict)  # name -> why
    senses_failed: set[str] = field(default_factory=set)  # two meanings, no per-claim answer


class EntityMatcher:
    """The four model stages over one corpus; clients are built only when first needed."""

    def __init__(
        self,
        corpus: ClaimCorpus,
        clusterer: EntityClusterer,
        record: MatchRecord,
        logger: logging.Logger,
        *,
        embed: EmbedFunction | None = None,
        classifier: Classifier | None = None,
        llm: LLMClient | None = None,
        dry: bool = False,
    ) -> None:
        self.corpus = corpus
        self.clusterer = clusterer
        self.record = record
        self.logger = logger
        self.sampler = ClaimSampler(corpus.claims_by_id)
        self._embed = embed
        self._classifier = classifier
        self._llm = llm
        self.dry = dry
        self.counts: Counter[str] = Counter()
        self.tally = Tally()

    # --- clients --------------------------------------------------------------

    @property
    def embed(self) -> EmbedFunction:
        if self._embed is None:
            self._embed = default_embed(dry=self.dry)
        return self._embed

    @property
    def classifier(self) -> Classifier:
        if self._classifier is None:
            self._classifier = Classifier.for_stage(SCREEN_CONFIG["stage"], logger=self.logger)
        return self._classifier

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.for_stage(JUDGE_CONFIG["stage"], logger=self.logger,
                                            max_tokens=JUDGE_CONFIG["max_output_tokens"])
        return self._llm

    # --- the names in play ----------------------------------------------------

    def names_in_play(self, new_members: Sequence[dict[str, Any]], existing: Existing
                      ) -> tuple[list[dict[str, Any]], dict[str, list[str]], list[list[str]]]:
        """One member per distinct name (new first), each name's claims, and the fixed units
        (seed entries; the existing entities with two or more names)."""

        claims_of: dict[str, set[str]] = {}
        biggest: dict[str, dict[str, Any]] = {}
        by_entity: dict[int, set[str]] = {}
        for entity, member in existing:
            name = member["name"]
            claims_of.setdefault(name, set()).update(member.get("claim_ids") or [])
            if name not in biggest or (member.get("count") or 0) > (biggest[name].get("count") or 0):
                biggest[name] = member
            by_entity.setdefault(id(entity), set()).add(name)
        members = [m for m in new_members if m["name"] not in biggest]
        for member in members:
            claims_of[member["name"]] = set(member.get("claim_ids") or [])
        members += [{**biggest[n], "claim_ids": sorted(claims_of[n])} for n in sorted(biggest)]
        seeds: dict[str, list[str]] = {}
        for member in members:
            seed_id = self.clusterer.norm_to_seed.get(normalize_name(member["name"]))
            if seed_id:
                seeds.setdefault(seed_id, []).append(member["name"])
        units = [sorted(names) for names in seeds.values() if len(names) > 1]
        units += [sorted(names) for names in by_entity.values() if len(names) > 1]
        return members, {n: sorted(c) for n, c in claims_of.items()}, units

    # --- stages ---------------------------------------------------------------

    def _net(self, members: list[dict[str, Any]], new: set[str]) -> dict[PairKey, Candidate] | None:
        net = CandidateNet(members, new, self.clusterer.index(members), self.sampler, self.embed)
        try:
            pairs = net.pairs()
        except EmbeddingsMissing:
            for texts in net.texts().values():
                try:
                    self.embed(texts, NET_CONFIG["embedding_kind"])
                except EmbeddingsMissing as missing:
                    self.tally.missing_embeddings(missing.texts)
            self.tally.meaning_unknown(len(net.new))
            pairs = net.pairs(meaning=False)
        except RuntimeError as exc:  # the embedder's error after its own retries
            self.logger.error(f"Entity net: the embeddings failed ({exc}); the new names keep "
                              "the spelling rules")
            return None
        self.counts["candidate_pairs"] += len(pairs)
        self.counts["strong_pairs"] += sum(1 for c in pairs.values() if c.strong)
        for source in (*SPELLING_SOURCES, *MEANING_SOURCES):
            self.counts[f"pairs_from_{source}"] += sum(1 for c in pairs.values()
                                                      if source in c.sources)
        return pairs

    def _edges(self, pairs: Mapping[PairKey, Candidate], claims_of: Mapping[str, list[str]],
               result: MatchResult) -> tuple[dict[PairKey, float], dict[PairKey, float]]:
        """The pairs that reach the judge (weights) and, dry, the pairs not yet screened."""

        edges = {p: STRONG_WEIGHT + c.similarity for p, c in pairs.items() if c.strong}
        weak = [p for p, c in pairs.items() if not c.strong]
        screen = PairScreen(self.classifier, self.sampler, claims_of, self.logger)
        answered = screen.screen(weak, dry=self.dry)
        bar = MATCH_CONFIG["screen_probability"]
        edges.update({p: prob for p, prob in answered.probabilities.items() if prob >= bar})
        self.counts["screened_pairs"] += len(answered.probabilities)
        self.counts["screen_dropped"] += sum(1 for p in answered.probabilities.values() if p < bar)
        self.counts["screen_requests"] += answered.requests
        self.tally.screen(answered.uncached, answered.chars)
        for pair in answered.failed:
            result.unsure.append({"names": list(pair), "reason": "", "source": SOURCE_UNSCREENED})
        return edges, dict.fromkeys(answered.uncached, 0.0)

    def _bundles(self, names: Sequence[str], edges: Mapping[PairKey, float], units: list[list[str]],
                 new: set[str]) -> tuple[list[Bundle], list[PairKey]]:
        bundles, cut = build_bundles(names, edges, units)
        bundles = [b for b in bundles if new & set(b.names)]
        oversized = sum(1 for b in bundles if len(b.names) > JUDGE_CONFIG["max_bundle_names"])
        if oversized:
            self.logger.warning(f"Entity judge: {oversized} fixed units are larger than the "
                                "bundle cap; each is judged whole")
        return bundles, [p for p in cut if new & set(p)]

    def _judged(self, bundles: Sequence[Bundle], claims_of: Mapping[str, list[str]], new: set[str],
                result: MatchResult) -> dict[str, Verdict]:
        judge = Judge(self.llm, self.sampler, claims_of, self.logger)
        answered = judge.judge(bundles, batch=JUDGE_CONFIG["batch"], dry=self.dry)
        self.counts["bundles"] += len(bundles)
        self.counts["judge_requests"] += answered.requests
        self.counts["judge_failed"] += len(answered.failed)
        self.tally.judge(answered.uncached)
        for bundle in bundles:
            verdict = answered.verdicts.get(bundle.bundle_id)
            if verdict is None:
                if not self.dry:
                    result.unjudged.update(dict.fromkeys(new & set(bundle.names), WHY_JUDGE))
                continue
            result.judged |= new & (set(bundle.names) - set(verdict.omitted))
            result.unjudged.update(dict.fromkeys(new & set(verdict.omitted), WHY_OMITTED))
            for number, group in enumerate(verdict.groups, 1):
                result.groups.append({"group_id": f"{bundle.bundle_id}/{number}",
                                      "bundle_id": bundle.bundle_id, "label": group.label,
                                      "names": group.names})
            result.unsure += [{"names": list(pair), "reason": reason, "source": SOURCE_JUDGE}
                              for pair, reason in verdict.unsure]
        return answered.verdicts

    def _two_meanings(self, result: MatchResult, verdicts: Mapping[str, Verdict],
                      claims_of: Mapping[str, list[str]], new: set[str]) -> None:
        groups_of: dict[str, list[dict[str, Any]]] = {}
        for group in result.groups:
            for name in group["names"]:
                groups_of.setdefault(name, []).append(group)
        shared = sorted({n for v in verdicts.values() for n in v.shared()} & new)
        jobs = [SenseJob(name, tuple(claims_of[name]),
                         tuple((g["label"], tuple(g["names"])) for g in groups_of[name]))
                for name in shared]
        if not jobs:
            return
        answered = self._splitter().split(jobs, batch=JUDGE_CONFIG["batch"], dry=self.dry)
        self.counts["two_meaning_names"] += len(jobs)
        self.counts["senses_requests"] += answered.requests
        self.tally.senses(answered.uncached)
        if self.dry:
            return
        entries = []
        for job in jobs:
            targets = [g["group_id"] for g in groups_of[job.name]]
            others = [sum(len(claims_of.get(n) or []) for n in g["names"] if n != job.name)
                      for g in groups_of[job.name]]
            picked, unclear, failed = self.divide(job, answered.answers.get(job.name), others)
            result.senses[job.name] = {c: [targets[i] for i in ix] for c, ix in picked.items()}
            if failed:
                result.senses_failed.add(job.name)
            entries.append({"name": job.name,
                            "targets": [{"id": g["group_id"], "label": g["label"]}
                                        for g in groups_of[job.name]],
                            "claims": result.senses[job.name], "unclear": unclear,
                            "failed": failed})
        self.record.add_senses(entries)

    def _splitter(self) -> SenseSplitter:
        return SenseSplitter(self.llm, self.sampler, self.corpus.claims_by_id, self.logger)

    @staticmethod
    def divide(job: SenseJob, answers: Mapping[str, list[int]] | None, fallback: Sequence[int]
               ) -> tuple[dict[str, list[int]], list[str], bool]:
        """``(claim -> group indexes, unclear claims, failed)``. A claim about several groups
        is in each of them; an unclear claim goes to the group that got the most claims of the
        name; without an answer every claim goes to the group whose ``fallback`` weight is
        highest (first on a tie)."""

        if answers is None:
            best = max(range(len(job.groups)), key=lambda i: (fallback[i], -i))
            return {c: [best] for c in job.claim_ids}, list(job.claim_ids), True
        got = Counter(i for indexes in answers.values() for i in indexes)
        best = max(range(len(job.groups)), key=lambda i: (got.get(i, 0), -i))
        unclear = [c for c in job.claim_ids if not answers.get(c)]
        return ({c: list(answers.get(c) or [best]) for c in job.claim_ids}, unclear, False)

    # --- entry points -----------------------------------------------------------

    def match(self, new_members: Sequence[dict[str, Any]], existing: Existing) -> MatchResult:
        """Run the four stages for ``new_members``; ``existing`` are the (entity, member)
        pairs new names may join."""

        result = MatchResult()
        if not new_members:
            return result
        members, claims_of, units = self.names_in_play(new_members, existing)
        new = {m["name"] for m in new_members}
        self.counts["new_names"] += len(new)
        pairs = self._net(members, new)
        if pairs is None:
            result.unjudged = dict.fromkeys(sorted(new), WHY_NET)
            return self._recorded(result)
        edges, unscreened = self._edges(pairs, claims_of, result)
        names = [m["name"] for m in members]
        bundles, cut = self._bundles(names, edges, units, new)
        if self.dry and unscreened:
            high, _ = self._bundles(names, {**edges, **unscreened}, units, new)
            self.tally.judge_high(Judge(self.llm, self.sampler, claims_of, self.logger),
                                  [b for b in high if b.bundle_id not in
                                   {x.bundle_id for x in bundles}])
        self.counts["cut_pairs"] += len(cut)
        result.unsure += [{"names": list(p), "reason": "", "source": SOURCE_CUT} for p in cut]
        verdicts = self._judged(bundles, claims_of, new, result)
        in_bundles = {n for b in bundles for n in b.names}
        result.judged |= new - in_bundles
        self._two_meanings(result, verdicts, claims_of, new)
        result.judged -= set(result.unjudged)
        self.counts["judged_names"] += len(result.judged)
        self.counts["unjudged_names"] += len(result.unjudged)
        return self._recorded(result)

    def _recorded(self, result: MatchResult) -> MatchResult:
        if not self.dry:
            self.record.add_groups(result.groups)
            self.record.add_unsure(result.unsure)
            self.record.set_unjudged(result.judged, result.unjudged)
        return result

    def divide_known(self, jobs: Sequence[SenseJob], target_ids: Mapping[str, Sequence[str]]
                     ) -> dict[str, dict[str, list[int]]]:
        """Update path: divide the new claims of known names held by several entities.
        ``job.groups`` are those entities, biggest member first (the unclear claims and, when
        the call fails, all of them go there); ``target_ids`` names their entity ids."""

        if not jobs:
            return {}
        answered = self._splitter().split(jobs, batch=JUDGE_CONFIG["batch"], dry=self.dry)
        self.counts["known_two_meaning_names"] += len(jobs)
        self.counts["senses_requests"] += answered.requests
        self.tally.senses(answered.uncached)
        out: dict[str, dict[str, list[int]]] = {}
        entries = []
        for job in jobs:
            weights = [len(job.groups) - i for i in range(len(job.groups))]
            picked, unclear, failed = self.divide(job, answered.answers.get(job.name), weights)
            if answered.answers.get(job.name) is not None:
                picked = {c: [0] if c in unclear else ix for c, ix in picked.items()}
            out[job.name] = picked
            ids = target_ids[job.name]
            entries.append({"name": job.name,
                            "targets": [{"id": ids[i], "label": label}
                                        for i, (label, _) in enumerate(job.groups)],
                            "claims": {c: [ids[i] for i in ix] for c, ix in picked.items()},
                            "unclear": unclear, "failed": failed})
        if not self.dry:
            self.record.add_senses(entries)
        return out

    def estimate(self) -> dict[str, Any]:
        """The dry run's ``Tally`` report (batch price when the judge uses the batch API)."""

        return self.tally.report(batch=JUDGE_CONFIG["batch"] and self.llm.supports_batch())

    def usage(self) -> dict[str, int]:
        """The chat tokens the API reported in this run (cached answers add nothing)."""

        if self._llm is None:
            return {}
        keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens")
        return {key: sum(int(entry.get(key) or 0) for entry in self._llm.usage) for key in keys}
