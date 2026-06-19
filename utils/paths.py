"""Resolve pipeline artifact paths: prefer human-reviewed edits when present."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

BACKUPS_DIR = Path("data/backups")


@dataclass(frozen=True)
class Artifact:
    original: Path
    edited: Path


THREADS = Artifact(Path("data/threads.json"), Path("data/threads_edited.json"))
CLASSIFIED = Artifact(
    Path("data/threads_classified.json"),
    Path("data/threads_classified_edited.json"),
)
CLAIMS = Artifact(Path("data/claims.json"), Path("data/claims_edited.json"))
AGGREGATED = Artifact(
    Path("data/claims_aggregated.json"),
    Path("data/claims_aggregated_edited.json"),
)
PLAN = Artifact(Path("data/wiki_plan.json"), Path("data/wiki_plan_edited.json"))

# ponytail: named aliases for call-site readability
ORIGINAL_THREADS_PATH = THREADS.original
EDITED_THREADS_PATH = THREADS.edited
ORIGINAL_CLASSIFIED_PATH = CLASSIFIED.original
EDITED_CLASSIFIED_PATH = CLASSIFIED.edited
ORIGINAL_CLAIMS_PATH = CLAIMS.original
EDITED_CLAIMS_PATH = CLAIMS.edited
ORIGINAL_AGGREGATED_PATH = AGGREGATED.original
EDITED_AGGREGATED_PATH = AGGREGATED.edited
ORIGINAL_PLAN_PATH = PLAN.original
EDITED_PLAN_PATH = PLAN.edited


def resolve(artifact: Artifact) -> Path:
    return artifact.edited if artifact.edited.exists() else artifact.original


def resolve_threads_path() -> Path:
    return resolve(THREADS)


def resolve_classified_path() -> Path:
    return resolve(CLASSIFIED)


def resolve_claims_path() -> Path:
    return resolve(CLAIMS)


def resolve_aggregated_path() -> Path:
    return resolve(AGGREGATED)


def resolve_plan_path() -> Path:
    return resolve(PLAN)


def edited_output_threads_path(source: Path | None = None) -> Path:
    del source
    return EDITED_THREADS_PATH


def edited_output_classified_path(source: Path | None = None) -> Path:
    del source
    return EDITED_CLASSIFIED_PATH


def has_classification_data() -> bool:
    return EDITED_CLASSIFIED_PATH.is_file() or ORIGINAL_CLASSIFIED_PATH.is_file()


def init_edited(artifact: Artifact, *, required: bool = True) -> Path | None:
    """Create ``artifact.edited`` from ``artifact.original`` if missing."""
    if artifact.edited.exists():
        return None
    if not artifact.original.is_file():
        if required:
            raise FileNotFoundError(
                f"Cannot create {artifact.edited}: {artifact.original} not found."
            )
        return None
    artifact.edited.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact.original, artifact.edited)
    return artifact.edited


def init_threads_edited() -> Path | None:
    try:
        return init_edited(THREADS, required=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Cannot create {EDITED_THREADS_PATH}: "
            f"{ORIGINAL_THREADS_PATH} not found. Run threads_split first."
        ) from exc


def init_classified_edited() -> Path | None:
    return init_edited(CLASSIFIED, required=False)


def init_claims_edited() -> Path | None:
    try:
        return init_edited(CLAIMS, required=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Cannot create {EDITED_CLAIMS_PATH}: "
            f"{ORIGINAL_CLAIMS_PATH} not found. Run extract first."
        ) from exc


def init_plan_edited() -> Path | None:
    return init_edited(PLAN, required=False)


def init_aggregated_edited() -> Path | None:
    try:
        return init_edited(AGGREGATED, required=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Cannot create {EDITED_AGGREGATED_PATH}: "
            f"{ORIGINAL_AGGREGATED_PATH} not found. Run aggregate first."
        ) from exc


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
    """Ensure the review workspace exists for the tagging tool and pipeline."""
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
