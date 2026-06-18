"""Resolve claims JSON paths: prefer human-reviewed edits when present."""

from __future__ import annotations

import shutil
from pathlib import Path

ORIGINAL_CLAIMS_PATH = Path("data/claims.json")
EDITED_CLAIMS_PATH = Path("data/claims_edited.json")
BACKUPS_DIR = Path("data/backups")


def resolve_claims_path() -> Path:
    return EDITED_CLAIMS_PATH if EDITED_CLAIMS_PATH.exists() else ORIGINAL_CLAIMS_PATH


def init_claims_edited() -> Path | None:
    """Create ``claims_edited.json`` from ``claims.json`` if missing."""
    if EDITED_CLAIMS_PATH.exists():
        return None
    if not ORIGINAL_CLAIMS_PATH.is_file():
        raise FileNotFoundError(
            f"Cannot create {EDITED_CLAIMS_PATH}: "
            f"{ORIGINAL_CLAIMS_PATH} not found. Run extract first."
        )
    EDITED_CLAIMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL_CLAIMS_PATH, EDITED_CLAIMS_PATH)
    return EDITED_CLAIMS_PATH
