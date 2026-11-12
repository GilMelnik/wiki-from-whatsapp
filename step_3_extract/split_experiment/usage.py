"""What the split experiment's calls cost: estimated by characters before a run (no
credit-gated endpoint), and as the API reported it after.

The estimate counts every request as one fresh call (an upper bound: answers already in the
disk cache cost nothing): input tokens = the system prompt and the user prompt by characters
(``update.estimate.chars_per_token``), output tokens = ``extract_output_per_conversation_token``
times the tokens of the unit's own messages (background and instructions write no claims),
priced at the extract stage's model, with ``batch_discount`` for the batch API
(``step_10_update.estimate``).

The reported cost is priced from the usage the provider returned (``LLMClient.usage``): fresh
input, prompt-cache writes and reads (``cache_write_price_factor`` / ``cache_read_price_factor``
times the input price) and output, with the batch discount when the call went through the
batch API. ``UsageLedger`` keeps that usage per request across runs, so a repeated run that the
disk cache answers for free still shows what the answers cost when they were made.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from step_10_update.estimate import ESTIMATE_CONFIG, CharTokenCounter, stage_model, usd
from step_3_extract.prompt import EXTRACT_SYSTEM
from step_3_extract.split_experiment.prompts import UnitJob
from step_3_extract.split_experiment.units import EXPERIMENT_CONFIG
from utils.json_io import write_json_file
from utils.reconcile import fingerprint

PRICED_MODEL = stage_model("extract")
TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
              "output_tokens")


def estimate(jobs: Sequence[UnitJob], use_batch: bool) -> dict[str, Any]:
    """Calls, input / output tokens by characters and dollars of these jobs."""
    count = CharTokenCounter()
    input_tokens = sum(count(EXTRACT_SYSTEM, job.prompt()) for job in jobs)
    conversation = sum(
        math.ceil(job.messages.rendered_chars(job.unit.message_ids) / count.chars_per_token)
        for job in jobs)
    output_tokens = math.ceil(conversation * ESTIMATE_CONFIG["extract_output_per_conversation_token"])
    cost = usd(PRICED_MODEL, input_tokens, output_tokens)
    if use_batch:
        cost *= ESTIMATE_CONFIG["batch_discount"]
    return {"calls": len(jobs), "input_tokens": input_tokens, "output_tokens": output_tokens,
            "conversation_tokens": conversation, "usd": round(cost, 4)}


def summed(entries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {key: sum(int(entry.get(key) or 0) for entry in entries) for key in TOKEN_KEYS}


def reported_usd(usage: Mapping[str, Any], batch: bool) -> float:
    """Dollars of one request's reported usage (``TOKEN_KEYS``)."""
    fresh = (usage["input_tokens"]
             + usage["cache_creation_input_tokens"] * EXPERIMENT_CONFIG["cache_write_price_factor"]
             + usage["cache_read_input_tokens"] * EXPERIMENT_CONFIG["cache_read_price_factor"])
    cost = usd(PRICED_MODEL, fresh, usage["output_tokens"])
    return cost * ESTIMATE_CONFIG["batch_discount"] if batch else cost


class UsageLedger:
    """Reported usage per request, ``{key: {TOKEN_KEYS..., batch, usd}}``, in a JSON file.

    A request's key covers the model and both prompts, so a changed prompt or another model is
    a new request; a request answered again (new usage) replaces its entry.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})

    @staticmethod
    def key(model: str, system: str, user: str) -> str:
        return fingerprint([model, system, user])

    def add(self, key: str, responses: Sequence[Mapping[str, Any]], batch: bool) -> None:
        usage = summed(responses)
        self.entries[key] = {**usage, "batch": batch, "usd": reported_usd(usage, batch)}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def save(self) -> None:
        write_json_file(self.entries, self.path)
