"""What a paid step would process and roughly cost, without calling any paid endpoint.

Each paid step has an estimate mode that plans its run and makes no model call (steps 3, 7,
8: ``run(estimate=True, count_tokens=...)``; steps 6b and 8b: ``run(estimate=True)``). The Anthropic
token counter needs credits, so the prompts are counted here by characters
(``update.estimate.chars_per_token``). Output tokens are a typical figure per call
(``output_tokens_per_call``), except for step 3 (``step_3_extract.price``, calibrated on the API's
reported usage, task 55): its Hebrew conversations are counted at their own
``extract_chars_per_token``, the instructions every call repeats
(``extract_instruction_tokens``) are read from the prompt cache at
``cache_read_price_factor`` of the input price, and the claims it writes grow with the
conversations it reads, not with the number of calls: output is ``extract_output_ratio``
times the conversations' tokens. Prices are ``usd_per_million_tokens`` per model, with
``batch_discount`` when step 3 or step 8 uses the batch API. Step 8's Hebrew prompts are
counted with their own measured ratio (``wiki_review.chars_per_token``), one output figure per
due review pass. Step 8b, the Hebrew language edit (one call per page holding text that was
never proofread), reads the page body: its prompt is counted at the review's ratio too, output
at the typical figure of ``output_tokens_per_call.proofread``, at the batch discount when it
uses the batch API.
Every figure is therefore rough; the estimate says what it counted.

``paid_calls`` counts chat-model calls only. The cheap classifier (Jev) and the embeddings are
priced but never stop the update: steps 1, 2, 4 and 5 use only them and run without a flag
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
from step_6_plan.assign_proposals import PROPOSALS_CONFIG
from step_6_plan.assign_rules import ASSIGN_CONFIG
from step_7_write_pages.run import UPDATE_CONFIG as PAGE_UPDATE_CONFIG
from step_7_write_pages.run import WRITE_CONFIG
from step_8_review.inputs import REVIEW_CONFIG
from step_8_review.parts import review_usd
from step_8_review.proofreader import PROOFREAD_CONFIG
from utils.config import section
from utils.llm_client.settings import CLASSIFIER_CONFIG
from utils.model_prices import ESTIMATE_CONFIG, stage_model, usd
from utils.paths import STEP_3, STEP_6_ASSIGN, STEP_7, STEP_8, STEP_8_PROOFREAD
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
            STEP_6_ASSIGN: self._assignments,
            STEP_7: self._pages,
            STEP_8: self._review,
            STEP_8_PROOFREAD: self._proofread,
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

    def _assignments(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True)
        classifier_model = CLASSIFIER_CONFIG["stages"][ASSIGN_CONFIG["stage"]]["model"]
        classifier_tokens = result["groups_asked"] * ESTIMATE_CONFIG["classifier_tokens_per_group"]
        calls = result["proposal_calls_max"]
        proposal_cost = calls * usd(
            stage_model(PROPOSALS_CONFIG["stage"]),
            ESTIMATE_CONFIG["proposal_input_tokens"],
            ESTIMATE_CONFIG["output_tokens_per_call"]["propose"],
        )
        plan_note = ("; the plan changed, so more groups may be asked"
                     if result["plan_changed"] else "")
        return Estimate(
            key, calls, usd(classifier_model, classifier_tokens) + proposal_cost,
            f"{result['groups_asked']} groups for the classifier, at most {calls} proposal "
            f"calls (up to {result['proposal_candidates_max']} groups could stay unplaced)"
            f"{plan_note}",
            "classifier tokens at a typical figure per group; proposal calls as an upper bound "
            "at a typical size",
        )

    def _pages(self, run: RunFunction, key: str) -> Estimate:
        result = run(estimate=True, count_tokens=self._counter)
        stages = {"write": WRITE_CONFIG["stage"], "update": PAGE_UPDATE_CONFIG["stage"]}
        cost = sum(
            usd(stage_model(stages[row["mode"]]), row["input_tokens"],
                ESTIMATE_CONFIG["output_tokens_per_call"][stages[row["mode"]]])
            for row in result["estimate"]
        )
        modes = result["modes"]
        return Estimate(
            key, len(result["estimate"]), cost,
            f"{len(result['estimate'])} pages to write ({modes.get('write', 0)} in full, "
            f"{modes.get('update', 0)} updated), {modes.get('keep', 0)} untouched",
            f"{result['input_tokens']} input tokens by characters, output at a typical figure "
            "per page (+ retries)",
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
