"""LLM-client configuration, sourced from the shared project config reader.

The single root ``config.json`` is parsed once in :mod:`utils.config`; here we
re-expose its ``llm_client`` section as ``CONFIG`` (so existing ``CONFIG[...]``
reads keep working) alongside ``LOGGING_CONFIG`` / ``DATA_DIR`` for callers that
still import them from this module. Only real secrets (API keys) belong in
``.env``, which :mod:`utils.config` loads so each provider SDK and the web-search
key check can read them from the environment.
"""

from __future__ import annotations

import os

from utils.config import DATA_DIR, LLM_CONFIG as CONFIG, LOGGING_CONFIG

__all__ = ["CONFIG", "DATA_DIR", "LOGGING_CONFIG", "web_search_enabled"]


def web_search_enabled(*, explicit: bool | None = None) -> bool:
    """Return whether Gemini Google Search grounding should run during generate.

    Priority: explicit ``False`` wins, then the ``enable_web_search`` config
    flag, then a fallback on whether a Gemini/Google API key is present.
    """

    if explicit is False:
        return False
    flag = CONFIG["enable_web_search"]  # None -> decide by key presence
    if flag is False:
        return False
    if flag is True:
        return True
    return bool(os.environ.get("GOOGLE_API_KEY"))
