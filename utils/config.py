"""Single project-wide configuration reader.

Loads ``.env`` (for secrets) and the root ``config.json`` exactly once. Access a
top-level section with ``section(name)``; the sections are ``logging``
(rotating-file/console tunables), ``llm_client`` (provider/model per stage,
cache/failure paths, batch/JSON knobs), ``entities`` (entity-clustering
thresholds and signal vocabulary), ``input`` (chat export location and cutoff
date), ``embedding``, ``classifier``, ``threads``, ``aggregate``, ``plan``,
``pages``, ``review``, ``site`` and ``update``. ``LOGGING_CONFIG``,
``LLM_CONFIG`` and ``ENTITIES_CONFIG`` are shortcuts for the first three.

``DATA_DIR`` is the parent folder for all pipeline artifacts. It comes from the
``WIKI_DATA_DIR`` environment variable (shell or ``.env``) when set, otherwise
from ``data_dir`` in ``config.json``. A relative value is resolved against
``PROJECT_ROOT``, never the current working directory. Every other module reads
config from here so the file is parsed one time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = PROJECT_ROOT / "config.json"

load_dotenv(PROJECT_ROOT / ".env", override=False)

with _CONFIG_PATH.open(encoding="utf-8") as _f:
    ROOT_CONFIG: dict[str, Any] = json.load(_f)


def section(name: str) -> dict[str, Any]:
    """Return the top-level ``config.json`` section ``name``."""
    try:
        return ROOT_CONFIG[name]
    except KeyError:
        raise KeyError(
            f"config.json has no section {name!r}; known: {sorted(ROOT_CONFIG)}"
        ) from None


def _resolve_data_dir() -> Path:
    path = Path(os.environ.get("WIKI_DATA_DIR") or ROOT_CONFIG["data_dir"]).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


DATA_DIR = _resolve_data_dir()
LOGGING_CONFIG = section("logging")
LLM_CONFIG = section("llm_client")
ENTITIES_CONFIG = section("entities")
