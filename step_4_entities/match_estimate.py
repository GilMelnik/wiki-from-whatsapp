"""The estimate of the model stages: calls, tokens and a dollar range, with no paid call.

The estimate runs the stages dry (``EntityMatcher(dry=True)``): the embeddings, screen answers
and judge answers already in the disk caches are used; whatever would be sent is counted here.
When something is not cached, what follows it cannot be known exactly, so each stage gets a
range:

* embeddings: the texts missing from the cache. Without them the meaning neighbours are
  unknown: the screen's upper bound adds ``neighbours`` pairs per new name and source;
* screen: the pairs not yet answered (low = high, plus the bound above);
* judge: low = the bundles of the pairs known to reach the judge (strong pairs, cached
  answers over the bar); high = also the bundles that appear when every unanswered pair
  passes, plus one possible bundle per new name whenever any embedding is missing (the meaning
  neighbours are then off for every name, so no new name's partners are known; that bundle is
  priced as the name and one partner of its size);
* two meanings: low = the calls of the cached verdicts; high = also a per-claim split of every
  new name that no cached verdict covers (each could be listed twice), at its own claims.

``exact`` is false whenever something was not cached. The update driver's pay gate reads
the HIGH end (``update_run.estimate``), so a call that may happen always asks first.

Tokens are characters / ``entities.match.estimate.chars_per_token`` (Hebrew claims); output
is a typical figure per call (``update.estimate.output_tokens_per_call``); prices from
``utils.model_prices``, at ``batch_discount`` when the judge uses the batch API. ``paid_calls``
counts the chat calls only (judge and two meanings), like step 10's other estimates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from step_4_entities.match_bundles import Bundle, bundle_id
from step_4_entities.match_claims import ClaimSampler
from step_4_entities.match_senses import SENSES_CONFIG, SENSES_SYSTEM
from utils.config import ENTITIES_CONFIG, section
from utils.embedder import Embedder, embed_texts
from utils.llm_client import BatchRequest
from utils.llm_client.settings import CLASSIFIER_CONFIG, CONFIG
from utils.model_prices import ESTIMATE_CONFIG, usd

if TYPE_CHECKING:
    from step_4_entities.match_judge import Judge
    from step_4_entities.match_net import EmbedFunction

MATCH_CONFIG = ENTITIES_CONFIG["match"]
MATCH_ESTIMATE = MATCH_CONFIG["estimate"]
OUTPUT_TOKENS = ESTIMATE_CONFIG["output_tokens_per_call"]


class EmbeddingsMissing(RuntimeError):
    """Texts the estimate would have to embed (it never does)."""

    def __init__(self, texts: Sequence[str]) -> None:
        super().__init__(f"{len(texts)} texts have no cached embedding")
        self.texts = list(texts)


class CacheOnlyEmbedder(Embedder):
    """The project embedder restricted to its disk cache: missing texts raise
    ``EmbeddingsMissing`` (no client is ever created)."""

    def _embed_missing(self, store: Any, kind: Any, keys: list[str], texts: list[str]) -> None:
        raise EmbeddingsMissing(texts)


def default_embed(*, dry: bool) -> EmbedFunction:
    """The configured embedder; ``dry``: from its cache only."""

    if not dry:
        return embed_texts
    embedder = CacheOnlyEmbedder.for_config()

    def cached(texts: Sequence[str], kind: str) -> np.ndarray:
        return embedder.embed_texts(texts, kind)  # type: ignore[arg-type]

    return cached


def _tokens(chars: int) -> int:
    return math.ceil(chars / MATCH_ESTIMATE["chars_per_token"])


def _request_chars(requests: Sequence[BatchRequest]) -> int:
    return sum(len(str(r.system)) + len(str(r.user)) for r in requests)


@dataclass
class Tally:
    """What a dry run would send, stage by stage."""

    embed_chars: int = 0
    embed_texts: int = 0
    meaning_unknown_names: int = 0
    screen_pairs: int = 0
    screen_chars: int = 0
    judge_low: list[BatchRequest] = field(default_factory=list)
    judge_extra: list[BatchRequest] = field(default_factory=list)
    senses_low: list[BatchRequest] = field(default_factory=list)
    judge_possible: list[int] = field(default_factory=lambda: [0, 0])  # calls, chars
    senses_possible: list[int] = field(default_factory=lambda: [0, 0])  # calls, chars

    def missing_embeddings(self, texts: Sequence[str]) -> None:
        self.embed_texts += len(texts)
        self.embed_chars += sum(len(t) for t in texts)

    def meaning_unknown(self, new_names: int) -> None:
        """The nearest neighbours of ``new_names`` cannot be known without the embeddings."""

        self.meaning_unknown_names += new_names

    def screen(self, pairs: Sequence[Any], chars: int) -> None:
        self.screen_pairs += len(pairs)
        self.screen_chars += chars

    def judge(self, requests: Sequence[BatchRequest]) -> None:
        self.judge_low += list(requests)

    def judge_high(self, judge: Judge, bundles: Sequence[Any]) -> None:
        """The extra bundles that appear when every unanswered pair passes the screen."""

        self.judge_extra += [r for r in (judge.request(b) for b in bundles)
                             if judge.llm.cached_json(r) is None]

    def senses(self, requests: Sequence[BatchRequest]) -> None:
        self.senses_low += list(requests)

    def possible(self, judge: Judge, uncertain: Sequence[str], unanswered: Sequence[str],
                 claims_of: Mapping[str, Sequence[str]]) -> None:
        """The calls unknown answers could still cause (see the module docstring)."""

        for name in uncertain:
            request = judge.request(Bundle(bundle_id([name]), [name]))
            self.judge_possible[0] += 1
            self.judge_possible[1] += len(str(request.system)) + 2 * len(str(request.user))
        sampler: ClaimSampler = judge.sampler
        for name in unanswered:
            claims = sampler.ordered(claims_of.get(name) or [])
            if not claims:
                continue
            calls = math.ceil(len(claims) / SENSES_CONFIG["claims_per_call"])
            lines = sum(len(sampler.line(c)) for c in claims)
            self.senses_possible[0] += calls
            self.senses_possible[1] += (calls * len(SENSES_SYSTEM)
                                        + lines * (1 + SENSES_CONFIG["context_claims"]))

    def report(self, *, batch: bool) -> dict[str, Any]:
        """Per stage ``{calls, tokens, usd}`` as ``[low, high]``, and the totals."""

        models = {"embedding": section("embedding")["model"],
                  "screen": CLASSIFIER_CONFIG["stages"][MATCH_CONFIG["screen"]["stage"]]["model"],
                  "judge": CONFIG["stages"][MATCH_CONFIG["judge"]["stage"]]["model"]}
        discount = ESTIMATE_CONFIG["batch_discount"] if batch else 1.0
        embed_tokens = _tokens(self.embed_chars)
        extra_pairs = self.meaning_unknown_names * MATCH_CONFIG["net"]["neighbours"] * 2
        pair_chars = self.screen_chars / self.screen_pairs if self.screen_pairs else 0.0
        screen_tokens = [_tokens(self.screen_chars),
                         _tokens(self.screen_chars + int(extra_pairs * pair_chars))]
        judge_calls = [len(self.judge_low),
                       len(self.judge_low) + len(self.judge_extra) + self.judge_possible[0]]
        judge_in = [_tokens(_request_chars(self.judge_low)),
                    _tokens(_request_chars([*self.judge_low, *self.judge_extra])
                            + self.judge_possible[1])]
        sense_calls = [len(self.senses_low), len(self.senses_low) + self.senses_possible[0]]
        senses_in = [_tokens(_request_chars(self.senses_low)),
                     _tokens(_request_chars(self.senses_low) + self.senses_possible[1])]

        def chat(calls: list[int], tokens: list[int], stage: str) -> list[float]:
            return [round(usd(models["judge"], t, c * OUTPUT_TOKENS[stage]) * discount, 4)
                    for c, t in zip(calls, tokens)]

        stages = {
            "embed": {"calls": [self.embed_texts] * 2, "tokens": [embed_tokens] * 2,
                      "usd": [round(usd(models["embedding"], embed_tokens), 4)] * 2},
            "screen": {"calls": [self.screen_pairs, self.screen_pairs + extra_pairs],
                       "tokens": screen_tokens,
                       "usd": [round(usd(models["screen"], t), 4) for t in screen_tokens]},
            "judge": {"calls": judge_calls, "tokens": judge_in,
                      "usd": chat(judge_calls, judge_in, "entity_judge")},
            "senses": {"calls": sense_calls, "tokens": senses_in,
                       "usd": chat(sense_calls, senses_in, "entity_senses")},
        }
        total = [round(sum(s["usd"][i] for s in stages.values()), 4) for i in (0, 1)]
        paid = [judge_calls[i] + sense_calls[i] for i in (0, 1)]
        exact = (total[0] == total[1] and paid[0] == paid[1] and not self.meaning_unknown_names
                 and not self.screen_pairs)
        return {"stages": stages, "usd": total, "paid_calls": paid, "models": models,
                "batch": batch, "exact": exact}
