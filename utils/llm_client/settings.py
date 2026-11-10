"""LLM-client configuration, sourced from the shared project config reader.

The single root ``config.json`` is parsed once in :mod:`utils.config`; here we
re-expose its ``llm_client`` section as ``CONFIG`` (so existing ``CONFIG[...]``
reads keep working) and its ``classifier`` section as ``CLASSIFIER_CONFIG``,
alongside ``LOGGING_CONFIG`` / ``DATA_DIR`` for callers that
still import them from this module. Only real secrets (API keys) belong in
``.env``, which :mod:`utils.config` loads so each provider SDK can read them from
the environment.
"""

from __future__ import annotations

from utils.config import DATA_DIR, LLM_CONFIG as CONFIG, LOGGING_CONFIG, section

CLASSIFIER_CONFIG = section("classifier")

__all__ = [
    "CLASSIFIER_CONFIG",
    "CONFIG",
    "DATA_DIR",
    "LOGGING_CONFIG",
]

