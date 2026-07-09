"""Configuration for the whole project, loaded from the root ``config.json``.

The single root ``config.json`` holds separate top-level sections:
``data_dir`` (the configurable parent folder for all pipeline artifacts),
``logging`` (rotating-file/console tunables), and ``llm_client`` (provider/model
per stage, cache/failure paths, batch poll interval, JSON-retry ceiling, cache
logging, Anthropic prompt-cache toggle, web-search toggle). It is loaded once;
the ``llm_client`` section is exposed as ``CONFIG`` so existing ``CONFIG[...]``
reads keep working, and ``LOGGING_CONFIG`` / ``DATA_DIR`` expose the other two.
Only real secrets (API keys) belong in ``.env``, which is loaded here so each
provider SDK and the web-search key check can read them from the environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = PROJECT_ROOT / "config.json"

load_dotenv(PROJECT_ROOT / ".env", override=False)

with _CONFIG_PATH.open(encoding="utf-8") as _f:
    ROOT_CONFIG = json.load(_f)

CONFIG = ROOT_CONFIG["llm_client"]
LOGGING_CONFIG = ROOT_CONFIG["logging"]
DATA_DIR = Path(ROOT_CONFIG["data_dir"])


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
