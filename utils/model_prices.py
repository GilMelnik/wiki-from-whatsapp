"""Rough dollar prices of model calls, without calling any paid endpoint.

Prices are ``update.estimate.usd_per_million_tokens`` per model (``usd``); ``stage_model`` is
the model a stage of ``llm.stages`` uses. The update's estimates (``step_10_update.estimate``),
the price of an extraction (``step_3_extract.price``) and of a review
(``step_8_review.parts.review_usd``) are built on them.
"""

from __future__ import annotations

from utils.config import LLM_CONFIG, section

ESTIMATE_CONFIG = section("update")["estimate"]


def usd(model: str, input_tokens: float, output_tokens: float = 0.0) -> float:
    prices = ESTIMATE_CONFIG["usd_per_million_tokens"]
    if model not in prices:
        raise KeyError(
            f"update.estimate.usd_per_million_tokens has no price for {model!r}; add it "
            f"(known: {sorted(prices)})"
        )
    price = prices[model]
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def stage_model(stage: str) -> str:
    return LLM_CONFIG["stages"][stage]["model"]
