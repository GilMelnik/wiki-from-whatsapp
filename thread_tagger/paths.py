"""Resolve thread data paths: prefer edited files when present."""

from __future__ import annotations

import shutil
from pathlib import Path

ORIGINAL_THREADS_PATH = Path("data/threads.json")
ORIGINAL_CLASSIFIED_PATH = Path("data/threads_classified.json")
EDITED_THREADS_PATH = Path("data/threads_edited.json")
EDITED_CLASSIFIED_PATH = Path("data/threads_classified_edited.json")
BACKUPS_DIR = Path("data/backups")


def resolve_threads_path() -> Path:
    return EDITED_THREADS_PATH if EDITED_THREADS_PATH.exists() else ORIGINAL_THREADS_PATH


def resolve_classified_path() -> Path:
    return (
        EDITED_CLASSIFIED_PATH
        if EDITED_CLASSIFIED_PATH.exists()
        else ORIGINAL_CLASSIFIED_PATH
    )


def edited_output_threads_path(source: Path | None = None) -> Path:
    """Where to write threads after editing."""
    del source
    return EDITED_THREADS_PATH


def edited_output_classified_path(source: Path | None = None) -> Path:
    """Where to write classification after editing."""
    del source
    return EDITED_CLASSIFIED_PATH


def has_classification_data() -> bool:
    """True when a classified JSON file exists (edited or original)."""
    return EDITED_CLASSIFIED_PATH.is_file() or ORIGINAL_CLASSIFIED_PATH.is_file()


def init_threads_edited() -> Path | None:
    """Create ``threads_edited.json`` from ``threads.json`` if missing."""
    if EDITED_THREADS_PATH.exists():
        return None
    if not ORIGINAL_THREADS_PATH.is_file():
        raise FileNotFoundError(
            f"Cannot create {EDITED_THREADS_PATH}: "
            f"{ORIGINAL_THREADS_PATH} not found. Run threads_split first."
        )
    EDITED_THREADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL_THREADS_PATH, EDITED_THREADS_PATH)
    return EDITED_THREADS_PATH


def init_classified_edited() -> Path | None:
    """Create ``threads_classified_edited.json`` if missing and source exists."""
    if EDITED_CLASSIFIED_PATH.exists():
        return None
    if not ORIGINAL_CLASSIFIED_PATH.is_file():
        return None
    EDITED_CLASSIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL_CLASSIFIED_PATH, EDITED_CLASSIFIED_PATH)
    return EDITED_CLASSIFIED_PATH


def init_edited_files(*, require_classified: bool = True) -> dict[str, Path]:
    """Create missing ``*_edited.json`` files by copying pipeline originals."""
    created: dict[str, Path] = {}

    threads = init_threads_edited()
    if threads:
        created["threads"] = threads

    classified = init_classified_edited()
    if classified:
        created["classified"] = classified
    elif require_classified and not has_classification_data():
        raise FileNotFoundError(
            f"{ORIGINAL_CLASSIFIED_PATH} not found. Run classify first, "
            "or start the tool with --inspect to browse threads only."
        )

    return created


def ensure_edited_workspace(
    *,
    classified_output: Path | None = None,
) -> dict[str, str]:
    """Ensure the review workspace exists for the tagging tool and pipeline.

    - Creates any missing ``*_edited.json`` from originals.
    - When ``classified_output`` is given (e.g. after ``classify.run``),
      copies that file into ``threads_classified_edited.json``.

    Returns human-readable action labels (e.g. ``{"threads_edited": "created"}``).
    """
    actions: dict[str, str] = {}

    if not EDITED_THREADS_PATH.exists() and ORIGINAL_THREADS_PATH.is_file():
        EDITED_THREADS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ORIGINAL_THREADS_PATH, EDITED_THREADS_PATH)
        actions["threads_edited"] = "created"

    if classified_output is not None and classified_output.is_file():
        EDITED_CLASSIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        existed = EDITED_CLASSIFIED_PATH.exists()
        shutil.copy2(classified_output, EDITED_CLASSIFIED_PATH)
        actions["threads_classified_edited"] = "updated" if existed else "created"
    elif not EDITED_CLASSIFIED_PATH.exists() and ORIGINAL_CLASSIFIED_PATH.is_file():
        EDITED_CLASSIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ORIGINAL_CLASSIFIED_PATH, EDITED_CLASSIFIED_PATH)
        actions["threads_classified_edited"] = "created"

    return actions
