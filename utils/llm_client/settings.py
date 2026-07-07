"""Configuration for the llm_client package.

All non-secret tunables live in the sibling ``config.json`` (provider/model per
stage, cache/failure paths, batch poll interval, JSON-retry ceiling, cache
logging, Anthropic prompt-cache toggle, web-search toggle). It is loaded once
into ``CONFIG``; consumers read
the keys they need from that single object rather than importing a constant per
value. Only real secrets (API keys) belong in ``.env``, which is loaded here so
each provider SDK and the web-search key check can read them from the environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

load_dotenv(PROJECT_ROOT / ".env", override=False)

with _CONFIG_PATH.open(encoding="utf-8") as _f:
    CONFIG = json.load(_f)


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
