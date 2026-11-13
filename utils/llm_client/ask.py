"""Many JSON requests to one stage: as one batch, or one sync call after the other."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from utils.llm_client.client import LLMClient
from utils.llm_client.models import BatchRequest


def ask_json(llm: LLMClient, requests: Sequence[BatchRequest], *, batch: bool,
             logger: logging.Logger) -> dict[str, Any]:
    """``{request id: parsed answer}``; a request without a usable answer is left out (and
    logged). ``batch``: one batch job (half price); otherwise one sync call per request, so
    that one failing call does not stop the others. A batch job that cannot be created raises
    the provider's error."""
    if batch:
        logger.info(f"{len(requests)} requests via the batch API")
        return llm.complete_batch_json(requests)
    answers: dict[str, Any] = {}
    for request in requests:
        try:
            answers[request.request_id] = llm.complete_json(
                request.system, request.user, task=request.task,
                response_schema=request.response_schema)
        # Provider errors have no common base class; one request must not stop the others.
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"The call of {request.request_id} failed: {exc!r}")
    return answers
