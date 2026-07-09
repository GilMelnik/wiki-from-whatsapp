"""Single project-wide configuration reader.

Loads ``.env`` (for secrets) and the root ``config.json`` exactly once and
exposes each top-level section: ``data_dir`` (the configurable parent folder for
all pipeline artifacts), ``logging`` (rotating-file/console tunables),
``llm_client`` (provider/model per stage, cache/failure paths, batch/JSON knobs),
and ``entities`` (entity-clustering thresholds and signal vocabulary). Every
other module reads config from here so the file is parsed one time.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = PROJECT_ROOT / "config.json"

load_dotenv(PROJECT_ROOT / ".env", override=False)

with _CONFIG_PATH.open(encoding="utf-8") as _f:
    ROOT_CONFIG = json.load(_f)

DATA_DIR = Path(ROOT_CONFIG["data_dir"])
LOGGING_CONFIG = ROOT_CONFIG["logging"]
LLM_CONFIG = ROOT_CONFIG["llm_client"]
ENTITIES_CONFIG = ROOT_CONFIG["entities"]
