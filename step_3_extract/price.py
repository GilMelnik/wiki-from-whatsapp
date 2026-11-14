"""The price of step 3's calls, calibrated on the API's reported usage (task 55).

The conversations are priced at the input price, the instructions every call repeats
(``update.estimate.extract_instruction_tokens``) are read from the prompt cache at
``cache_read_price_factor`` of it, and the claims written grow with the conversations read:
output is ``extract_output_ratio`` times the conversations' tokens; ``batch_discount`` with
the batch API. The update's estimate and step 3's split experiment price with it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from utils.model_prices import ESTIMATE_CONFIG, usd


@dataclass(frozen=True)
class ExtractPrice:
    """A step-3 run's tokens and dollars (``extract_price``)."""

    prompt_tokens: int  # the user prompts: conversations (+ notes, existing claims)
    cached_tokens: int  # the instructions, read from the prompt cache once per call
    output_tokens: int
    usd: float


def extract_price(
    model: str, calls: int, prompt_tokens: int, conversation_tokens: int, batch: bool
) -> ExtractPrice:
    """Price ``calls`` step-3 calls: ``prompt_tokens`` at the input price, every call's
    instructions at ``cache_read_price_factor`` of it, output = ``extract_output_ratio`` times
    the tokens of the messages claims are extracted from (``conversation_tokens``), all at the
    batch discount when ``batch``."""

    cached = calls * int(ESTIMATE_CONFIG["extract_instruction_tokens"])
    output = math.ceil(conversation_tokens * ESTIMATE_CONFIG["extract_output_ratio"])
    cost = usd(model, prompt_tokens + cached * ESTIMATE_CONFIG["cache_read_price_factor"], output)
    if batch:
        cost *= ESTIMATE_CONFIG["batch_discount"]
    return ExtractPrice(prompt_tokens, cached, output, cost)
