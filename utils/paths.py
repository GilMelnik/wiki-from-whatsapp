"""Resolve pipeline artifact paths: prefer human-reviewed edits when present.

Every artifact lives under ``<data_dir>/<step_folder>/<filename>`` where
``data_dir`` comes from the root ``config.json`` (default ``data``), so the
parent folder is a one-line config change. This module is the single source of
truth for those locations; nothing else hardcodes a ``data/...`` literal.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from utils.config import DATA_DIR

STEP_0 = "step_0_preprocessing"
STEP_1 = "step_1_threads_split"
STEP_2 = "step_2_classify"
STEP_3 = "step_3_extract"
STEP_4 = "step_4_entities"
STEP_5 = "step_5_aggregate"
STEP_6 = "step_6_plan"
STEP_7 = "step_7_community"
STEP_8 = "step_8_background"
STEP_9 = "step_9_site"
SHARED = "shared"


def step_path(step_folder: str, *parts: str) -> Path:
    return DATA_DIR.joinpath(step_folder, *parts)


@dataclass(frozen=True)
class Artifact:
    original: Path
    edited: Path


# --------------------------------------------------------------- step 0 inputs
CHAT_TXT_PATH = step_path(STEP_0, "_chat.txt")
CHAT_OLD_TXT_PATH = step_path(STEP_0, "_chat_old.txt")
CHATS_FROM_PHONE_DIR = step_path(STEP_0, "chats_from_phone")
CHAT_ANDROID_PATH = step_path(STEP_0, "chats_from_phone", "chat_android.json")
MESSAGES_PATH = step_path(STEP_0, "messages.json")
MESSAGES_OLD_PATH = step_path(STEP_0, "messages_old.json")
MESSAGES_COMBINED_PATH = step_path(STEP_0, "messages_combined.json")
SENDER_ID_TO_NICKNAME_PATH = step_path(STEP_0, "sender_id_to_nickname.json")

# ------------------------------------------------------- step 1 threads_split
TFIDF_CORPUS_PATH = step_path(STEP_1, "tfidf_corpus.json")
TFIDF_TOKENS_PATH = step_path(STEP_1, "tfidf_tokens.json")
MESSAGE_EMBEDDINGS_PATH = step_path(STEP_1, "message_embeddings.json")
MESSAGE_QUERY_EMBEDDINGS_PATH = step_path(STEP_1, "message_query_embeddings.json")

# ------------------------------------------------------- reviewed artifacts
THREADS = Artifact(
    step_path(STEP_1, "threads.json"), step_path(STEP_1, "threads_edited.json")
)
CLASSIFIED = Artifact(
    step_path(STEP_2, "threads_classified.json"),
    step_path(STEP_2, "threads_classified_edited.json"),
)
CLAIMS = Artifact(
    step_path(STEP_3, "claims.json"), step_path(STEP_3, "claims_edited.json")
)
ENTITIES = Artifact(
    step_path(STEP_4, "entities.json"), step_path(STEP_4, "entities_edited.json")
)
AGGREGATED = Artifact(
    step_path(STEP_5, "claims_aggregated.json"),
    step_path(STEP_5, "claims_aggregated_edited.json"),
)
PLAN = Artifact(
    step_path(STEP_6, "wiki_plan.json"), step_path(STEP_6, "wiki_plan_edited.json")
)
WIKI_PAGES = Artifact(
    step_path(STEP_7, "wiki_pages.json"), step_path(STEP_7, "wiki_pages_edited.json")
)

# ponytail: named aliases for call-site readability
ORIGINAL_THREADS_PATH = THREADS.original
EDITED_THREADS_PATH = THREADS.edited
ORIGINAL_CLASSIFIED_PATH = CLASSIFIED.original
EDITED_CLASSIFIED_PATH = CLASSIFIED.edited
ORIGINAL_CLAIMS_PATH = CLAIMS.original
EDITED_CLAIMS_PATH = CLAIMS.edited
ORIGINAL_ENTITIES_PATH = ENTITIES.original
EDITED_ENTITIES_PATH = ENTITIES.edited
ORIGINAL_AGGREGATED_PATH = AGGREGATED.original
EDITED_AGGREGATED_PATH = AGGREGATED.edited
ORIGINAL_PLAN_PATH = PLAN.original
EDITED_PLAN_PATH = PLAN.edited

# --------------------------------------------------------------- step 3 extract
AUDIT_DIR = step_path(STEP_3, "audit")
AUDIT_PATH = AUDIT_DIR / "claims_audit.json"

# -------------------------------------------------------------- step 4 entities
ENTITIES_SEED_PATH = step_path(STEP_4, "entities_seed.json")
ENTITY_ANALYSIS_PATH = step_path(STEP_4, "entity_claim_analysis.json")
ENTITY_DISTANCE_MATRIX_PATH = step_path(STEP_4, "entity_distance_matrix.npy")
ENTITY_DISTANCE_META_PATH = step_path(STEP_4, "entity_distance_matrix.json")
# Manual claim aggregations created in the entity reviewer (no pipeline original);
# consumed by step 5 to force-merge the grouped claims.
MANUAL_AGGREGATIONS_PATH = step_path(STEP_4, "claim_aggregations.json")

# ------------------------------------------------------------ step 5 aggregate
CLAIM_QUERY_EMBEDDINGS_PATH = step_path(STEP_5, "claim_query_embeddings.json")
CLAIM_PASSAGE_EMBEDDINGS_PATH = step_path(STEP_5, "claim_passage_embeddings.json")
CLAIM_DISTANCE_MATRIX_PATH = step_path(STEP_5, "claim_distance_matrix.npy")
CLAIM_DISTANCE_META_PATH = step_path(STEP_5, "claim_distance_matrix.json")

# ----------------------------------------------------------- step 8 background
DRAFTS_DIR = step_path(STEP_8, "drafts")

# ------------------------------------------------------------------- shared
BACKUPS_DIR = step_path(SHARED, "backups")


def resolve(artifact: Artifact) -> Path:
    return artifact.edited if artifact.edited.exists() else artifact.original


def resolve_threads_path() -> Path:
    return resolve(THREADS)


def resolve_classified_path() -> Path:
    return resolve(CLASSIFIED)


def resolve_claims_path() -> Path:
    return resolve(CLAIMS)


def resolve_entities_path() -> Path:
    return resolve(ENTITIES)


def resolve_aggregated_path() -> Path:
    return resolve(AGGREGATED)


def resolve_plan_path() -> Path:
    return resolve(PLAN)


def resolve_wiki_pages_path() -> Path:
    return resolve(WIKI_PAGES)


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


def init_entities_edited() -> Path | None:
    try:
        return init_edited(ENTITIES, required=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Cannot create {EDITED_ENTITIES_PATH}: "
            f"{ORIGINAL_ENTITIES_PATH} not found. Run entities first."
        ) from exc


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
