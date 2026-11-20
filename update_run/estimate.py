"""What a paid step would process and roughly cost, without calling any paid endpoint.

Each paid step has an estimate mode that plans its run and makes no model call (steps 3, 8,
9: ``run(estimate=True, count_tokens=...)``; steps 7 and 10: ``run(estimate=True)``). The Anthropic
token counter needs credits, so the prompts are counted here by characters
(``update.estimate.chars_per_token``). Output tokens are a typical figure per call
(``output_tokens_per_call``), except for step 3 (``step_3_extract.price``, calibrated on the API's
reported usage, task 55): its Hebrew conversations are counted at their own
``extract_chars_per_token``, the instructions every call repeats
(``extract_instruction_tokens``) are read from the prompt cache at
``cache_read_price_factor`` of the input price, and the claims it writes grow with the
conversations it reads, not with the number of calls: output is ``extract_output_ratio``
times the conversations' tokens. Prices are ``usd_per_million_tokens`` per model, with
``batch_discount`` when step 3, step 8 or step 9 uses the batch API. Step 9's Hebrew prompts are
counted with their own measured ratio (``wiki_review.chars_per_token``), one output figure per
due review pass. Step 10, the Hebrew language edit (one call per page holding text that was
never proofread), reads the page body: its prompt is counted at the review's ratio too, output
at the typical figure of ``output_tokens_per_call.proofread``, at the batch discount when it
uses the batch API.
Every figure is therefore rough; the estimate says what it counted.

Step 4 (``run(estimate=True)``, ``step_4_entities.match_estimate``) prices its model stages
itself, as a range: the judge and the per-claim calls (Opus) are its paid calls; the price and
the calls given here are the upper end of the range. So does step 5, the same-complaint check
(``step_5_same_complaint.run``, ``run(estimate=True)``): one Opus call per entity list
whose answer is missing or stale and not in the LLM disk cache, input by characters, output at
a low and a high figure per call (``same_complaint.estimate``); the upper end is shown.

``paid_calls`` counts chat-model calls only. The cheap classifier (Jev) and the embeddings are
priced but never stop the update: steps 1 and 2 use only them and run without a flag
(cents for an update of a few hundred messages).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from review_tool.steps import Step
from step_3_extract.price import extract_price
from step_3_extract.prompt import EXTRACT_SYSTEM
from step_7_assign.proposals import PROPOSALS_CONFIG
from step_7_assign.rules import ASSIGN_CONFIG
from step_8_write_pages.run import UPDATE_CONFIG as PAGE_UPDATE_CONFIG
from step_8_write_pages.run import WRITE_CONFIG
from step_9_review.inputs import REVIEW_CONFIG
from step_9_review.parts import review_usd
from step_10_proofread.proofreader import PROOFREAD_CONFIG
from utils.config import section
from utils.llm_client.settings import CLASSIFIER_CONFIG
from utils.model_prices import ESTIMATE_CONFIG, stage_model, usd
from utils.paths import (
    STEP_3,
    STEP_4,
    STEP_5,
    STEP_7,
    STEP_8,
    STEP_9,
    STEP_10,
)
from utils.token_counters import CharTokenCounter

EMBEDDING_MODEL = section("embedding")["model"]
RunFunction = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class Estimate:
    step: str
    paid_calls: int
    usd: float
    what: str
    counted: str

    def line(self) -> str:
        return f"{self.what}; about ${self.usd:.2f} (rough: {self.counted})"

    def to_json(self) -> dict[str, Any]:
        return {"step": self.step, "paid_calls": self.paid_calls, "usd": round(self.usd, 4),
                "what": self.what, "counted": self.counted}


def embedding_usd(texts: Sequence[str]) -> float:
    """Rough price of embedding ``texts`` once (cached vectors cost nothing)."""
    chars = sum(len(text) for text in texts)
    return usd(EMBEDDING_MODEL, chars / ESTIMATE_CONFIG["chars_per_token"])


class Estimator:
    """Runs a paid step's estimate mode and prices it; ``None`` for a step it cannot price."""

    def __init__(self, run_function: Callable[[Step], RunFunction] | None = None) -> None:
        self._run_function = run_function or (lambda step: step.run_function())
        self._counter = CharTokenCounter()
        self._extract_counter = CharTokenCounter(ESTIMATE_CONFIG["extract_chars_per_token"])
        self._review_counter = CharTokenCounter(REVIEW_CONFIG["chars_per_token"])
        self._by_step: Mapping[str, Callable[[RunFunction, str], Estimate]] = {
            STEP_3: self._claims,
            STEP_4: self._entities,
            STEP_7: self._assignments,
            STEP_5: self._same_complaint,
            STEP_8: self._pages,
            STEP_9: self._review,
            STEP_10: self._proofread,
        }

    def __call__(self, step: Step) -> Estimate | None:
        price = self._by_step.get(step.key)
        return None if price is None else price(self._run_function(step), step.key)

    def _claims(self, run: RunFunction, key: str) -> Estimate:
        # The counter sees the system prompt too: its share is taken out again, so that only
        # the user prompts (the conversations) are counted at the Hebrew ratio.
        result = run(estimate=True, count_tokens=self._extract_counter)
        calls = result["model_calls"]
        conversations = max(0, result["input_tokens"]
                            - calls * self._extract_counter(EXTRACT_SYSTEM, ""))
        price = extract_price(result["model"], calls, conversations, conversations,
                              result["batch_mode"])
        return Estimate(
            key, calls, price.usd,
            f"{calls} conversations to read ({result['update_calls']} of them already have "
            f"claims and are read for their new messages)",
            f"{conversations} conversation tokens by characters, {price.cached_tokens} tokens "
            f"of instructions read from the prompt cache, {price.output_tokens} output tokens "
            f"in proportion to the conversations' length"
            f"{', batch price' if result['batch_mode'] else ''}",
        )

    def _entities(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True)
        stages = result["stages"]

        def span(values: Sequence[int]) -> str:
            return str(values[0]) if values[0] == values[1] else f"{values[0]}-{values[1]}"

        return Estimate(
            key, result["paid_calls"][1], result["usd"][1],
            f"{span(stages['judge']['calls'])} judge calls and {span(stages['senses']['calls'])} "
            f"per-claim calls, {span(stages['screen']['calls'])} pairs for the classifier, "
            f"{stages['embed']['calls'][0]} texts to embed (${result['usd'][0]:.2f} to "
            f"${result['usd'][1]:.2f})",
            "the model stages run dry on the caches; tokens by characters, output at a typical "
            f"figure per call{', batch price' if result['batch'] else ''}; the upper end of the "
            "range is shown",
        )

    def _assignments(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True)
        classifier_model = CLASSIFIER_CONFIG["stages"][ASSIGN_CONFIG["stage"]]["model"]
        classifier_tokens = result["claims_asked"] * ESTIMATE_CONFIG["classifier_tokens_per_claim"]
        calls = result["proposal_calls_max"]
        proposal_cost = calls * usd(
            stage_model(PROPOSALS_CONFIG["stage"]),
            ESTIMATE_CONFIG["proposal_input_tokens"],
            ESTIMATE_CONFIG["output_tokens_per_call"]["propose"],
        )
        plan_note = ("; the plan changed, so more claims may be asked"
                     if result["plan_changed"] else "")
        return Estimate(
            key, calls, usd(classifier_model, classifier_tokens) + proposal_cost,
            f"{result['claims_asked']} claims for the classifier, at most {calls} proposal "
            f"calls (up to {result['proposal_candidates_max']} claims could stay unplaced)"
            f"{plan_note}",
            "classifier tokens at a typical figure per claim; proposal calls as an upper bound "
            "at a typical size",
        )

    def _same_complaint(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True)
        calls = result["estimate"]
        low, high = calls["usd"]
        what = (f"{calls['calls']} model call(s) for the entity lists whose answer is missing "
                f"or stale ({result['due']} of the {result['lists']} lists that can release a "
                f"claim; ${low:.2f} to ${high:.2f})" if calls["calls"]
                else f"no call: each of the {result['lists']} lists that can release a claim "
                     "has its answer")
        return Estimate(
            key, calls["calls"], high, what,
            f"{calls['input_tokens']} input tokens by characters, output at a low and a high "
            f"figure per call{', batch price' if calls['batch'] else ''}; the upper end of the "
            "range is shown",
        )

    def _pages(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True, count_tokens=self._counter)
        stages = {"write": WRITE_CONFIG["stage"], "update": PAGE_UPDATE_CONFIG["stage"]}
        cost = sum(
            usd(stage_model(stages[row["mode"]]), row["input_tokens"],
                ESTIMATE_CONFIG["output_tokens_per_call"][stages[row["mode"]]])
            for row in result["estimate"]
        )
        if result["batch"]:
            cost *= ESTIMATE_CONFIG["batch_discount"]
        modes = result["modes"]
        return Estimate(
            key, len(result["estimate"]), cost,
            f"{len(result['estimate'])} pages to write ({modes.get('write', 0)} in full, "
            f"{modes.get('update', 0)} updated), {modes.get('keep', 0)} untouched",
            f"{result['input_tokens']} input tokens by characters, output at a typical figure "
            f"per page (+ retries){', batch price' if result['batch'] else ''}",
        )

    def _review(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True, count_tokens=self._review_counter)
        calls = result["calls"]
        what = (f"{calls} of {len(result['passes'])} review passes due "
                f"({result['input_tokens']} input tokens)"
                if calls else "no call: nothing the review reads changed since the last review")
        return Estimate(key, calls, review_usd(result), what,
                        "input tokens by characters, output at a typical figure per pass"
                        f"{', batch price' if result['batch'] else ''}")

    def _proofread(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True)
        rows = result.get("estimate") or []  # none when proofreading is off
        stage = PROOFREAD_CONFIG["stage"]
        tokens = sum(math.ceil(row["chars"] / self._review_counter.chars_per_token)
                     for row in rows)
        cost = usd(stage_model(stage), tokens,
                   len(rows) * ESTIMATE_CONFIG["output_tokens_per_call"][stage])
        if result.get("batch"):
            cost *= ESTIMATE_CONFIG["batch_discount"]
        what = (f"{len(rows)} pages hold text that was never proofread "
                f"({sum(row['new_sentences'] for row in rows)} sentences)"
                if rows else "no call: no page holds text that was never proofread")
        return Estimate(key, len(rows), cost, what,
                        f"{tokens} input tokens by characters, output at a typical figure per "
                        f"page{', batch price' if result.get('batch') else ''}")
