"""What a planning run would send and roughly cost: ``python -m step_6_plan.run --estimate``.

No model call, nothing written. The prompt is the one the first planning call sends
(``step_6_plan.fit`` over the planning list, ``step_6_plan.claim_list``); its input tokens are
counted by the counting endpoint (free; the same count the step makes before every call).
How long the model's answer is cannot be known before, so output is a low and a high figure
per call (``plan.estimate.output_tokens_per_call``), priced with ``utils.model_prices.usd``
for the plan stage's model. A revision call (``plan.coverage.revise``) is priced like the
first call (its request holds the proposed plan and the findings too, so it reads somewhat
more); the ceiling is ``plan.max_model_calls`` such calls. The coverage check's classifier
calls are not priced.
"""

from __future__ import annotations

import logging
from typing import Any

from step_6_plan.claim_list import PlanningList
from step_6_plan.coverage import COVERAGE_CONFIG
from step_6_plan.fit import fitted_prompt
from step_6_plan.model import PLAN_CONFIG
from step_6_plan.prompt import initial_request
from utils.model_prices import usd
from utils.token_counters import TokenCounter

OUTPUT_TOKENS = PLAN_CONFIG["estimate"]["output_tokens_per_call"]


def _dollars(span: list[float]) -> str:
    return f"${span[0]:.2f}-${span[1]:.2f}"


def estimate_plan(planning: PlanningList, count_tokens: TokenCounter, model: str, *,
                  would_call: bool, logger: logging.Logger) -> dict[str, Any]:
    """The planning list's numbers, the first call's input tokens and the price ranges (see
    the module docstring). ``would_call``: a run would plan (no plan yet, or ``--rebuild``)."""

    prompt = fitted_prompt(planning.claims)
    shown, total = len(prompt.shown), len(planning.claims)
    tokens = count_tokens(prompt.system, prompt.user(initial_request(shown, total)))
    outputs = [OUTPUT_TOKENS["low"], OUTPUT_TOKENS["high"]]
    call = [round(usd(model, tokens, out), 4) for out in outputs]
    calls = PLAN_CONFIG["max_model_calls"]
    revision = call if COVERAGE_CONFIG["revise"] else None
    ceiling = [round(calls * end, 4) for end in call]
    refused = would_call and planning.refusal() is not None
    if not would_call:
        status = "A run would make no model call: a plan exists (--rebuild plans again)."
    elif refused:
        status = (f"Planning would refuse now: {planning.waiting()} for the same-complaint "
                  "check (python -m step_5_same_complaint.run).")
    else:
        status = "A run would plan now."
    logger.info(
        f"Plan estimate ({model}): {total} claims in the list, {planning.withheld_left_out} "
        f"withheld claims left out, the prompt shows {shown}; {tokens} input tokens (counting "
        f"endpoint); one planning call {_dollars(call)}, a revision call "
        f"{_dollars(revision) if revision else 'none (plan.coverage.revise is off)'}, at most "
        f"{calls} calls {_dollars(ceiling)} (output {outputs[0]}-{outputs[1]} tokens per call). "
        f"{status}"
    )
    return {"estimate": True, "written": False, "model_calls": 0, "model": model,
            "claim_count": total, "withheld_left_out": planning.withheld_left_out,
            "shown_claim_count": shown, "input_tokens": tokens, "output_tokens": outputs,
            "usd": {"call": call, "revision": revision, "ceiling": ceiling},
            "max_model_calls": calls, "waiting_lists": planning.waiting_lists,
            "would_call": would_call, "refused": refused}
