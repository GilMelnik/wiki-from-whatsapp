"""Resolve wiki plan and aggregated claims paths (prefer human edits)."""

from __future__ import annotations

import shutil
from pathlib import Path

ORIGINAL_PLAN_PATH = Path("data/wiki_plan.json")
EDITED_PLAN_PATH = Path("data/wiki_plan_edited.json")
ORIGINAL_AGGREGATED_PATH = Path("data/claims_aggregated.json")
EDITED_AGGREGATED_PATH = Path("data/claims_aggregated_edited.json")
BACKUPS_DIR = Path("data/backups")


def resolve_plan_path() -> Path:
    return EDITED_PLAN_PATH if EDITED_PLAN_PATH.exists() else ORIGINAL_PLAN_PATH


def resolve_aggregated_path() -> Path:
    return (
        EDITED_AGGREGATED_PATH
        if EDITED_AGGREGATED_PATH.exists()
        else ORIGINAL_AGGREGATED_PATH
    )


def init_plan_edited() -> Path | None:
    """Create ``wiki_plan_edited.json`` from ``wiki_plan.json`` if missing."""
    if EDITED_PLAN_PATH.exists():
        return None
    if not ORIGINAL_PLAN_PATH.is_file():
        return None
    EDITED_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL_PLAN_PATH, EDITED_PLAN_PATH)
    return EDITED_PLAN_PATH


def init_aggregated_edited() -> Path | None:
    """Create ``claims_aggregated_edited.json`` from aggregated output if missing."""
    if EDITED_AGGREGATED_PATH.exists():
        return None
    if not ORIGINAL_AGGREGATED_PATH.is_file():
        raise FileNotFoundError(
            f"Cannot create {EDITED_AGGREGATED_PATH}: "
            f"{ORIGINAL_AGGREGATED_PATH} not found. Run aggregate first."
        )
    EDITED_AGGREGATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL_AGGREGATED_PATH, EDITED_AGGREGATED_PATH)
    return EDITED_AGGREGATED_PATH
