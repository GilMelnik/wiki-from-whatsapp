"""Resolve pipeline artifact paths: prefer human-reviewed edits when present.

Every artifact lives under ``<data_dir>/<step_folder>/<filename>`` where
``data_dir`` comes from the root ``config.json`` (default ``data``), so the
parent folder is a one-line config change. This module is the single source of
truth for those locations; nothing else hardcodes a ``data/...`` literal.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from utils.config import DATA_DIR, PROJECT_ROOT, section

STEP_1 = "step_1_threads_split"
STEP_2 = "step_2_classify"
STEP_3 = "step_3_extract"
STEP_4 = "step_4_entities"
STEP_5 = "step_5_aggregate"
STEP_6 = "step_6_plan"
# The assignment step's id in the review tool's step registry; it writes into STEP_6's folder.
STEP_6_ASSIGN = "step_6_assign"
STEP_7 = "step_7_write_pages"
STEP_8 = "step_8_review"
STEP_9 = "step_9_site"
STEP_10 = "step_10_update"
SHARED = "shared"


def step_path(step_folder: str, *parts: str) -> Path:
    return DATA_DIR.joinpath(step_folder, *parts)


@dataclass(frozen=True)
class Artifact:
    original: Path
    edited: Path


# ------------------------------------------------- input (from the scraper)
# Produced by the whatsapp-group-scraper crypt15 exporter; copy it here.
INPUT_CONFIG = section("input")
CHAT_ANDROID_PATH = DATA_DIR / INPUT_CONFIG["export_filename"]
UPDATES_DIR = DATA_DIR / INPUT_CONFIG["updates_dir"]

# ------------------------------------------------------- step 1 threads_split
# The splitter's measurement against the gold windows (``step_1_threads_split.measure``).
MEASURE_REPORT_PATH = step_path(STEP_1, "measure_report.md")
# Hand-tagged gold clustering, frozen by ``step_1_threads_split.evaluate --freeze-gold``.
GOLD_THREADS_PATH = step_path(STEP_1, "gold_threads.json")
# Which gold windows the user marked as done in the thread tagger's gold mode.
GOLD_PROGRESS_PATH = step_path(STEP_1, "gold_progress.json")

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
# Which claim groups go on which page and carry which labels (contract C8).
ASSIGNMENTS = Artifact(
    step_path(STEP_6, "assignments.json"), step_path(STEP_6, "assignments_edited.json")
)
WIKI_PAGES = Artifact(
    step_path(STEP_7, "wiki_pages.json"), step_path(STEP_7, "wiki_pages_edited.json")
)
# The whole-wiki review's suggestions and the owner's decisions on them (contract C12).
SUGGESTIONS = Artifact(
    step_path(STEP_8, "suggestions.json"), step_path(STEP_8, "suggestions_edited.json")
)

# ponytail: named aliases for call-site readability
ORIGINAL_THREADS_PATH = THREADS.original
EDITED_THREADS_PATH = THREADS.edited
ORIGINAL_CLAIMS_PATH = CLAIMS.original

# --------------------------------------------------------------- step 3 extract
AUDIT_DIR = step_path(STEP_3, "audit")
AUDIT_PATH = AUDIT_DIR / "claims_audit.json"

# -------------------------------------------------------------- step 4 entities
ENTITIES_SEED_PATH = step_path(STEP_4, "entities_seed.json")
ENTITY_ANALYSIS_PATH = step_path(STEP_4, "entity_claim_analysis.json")
# Manual claim aggregations created in the entity reviewer (no pipeline original);
# step 5 treats each one as a must-link: its claims end up in one group.
MANUAL_AGGREGATIONS_PATH = step_path(STEP_4, "claim_aggregations.json")

# ------------------------------------------------------------ step 5 aggregate
# The measurement's private working folder (``python -m step_5_aggregate.evaluate``).
AGGREGATE_EVAL_DIR = step_path(STEP_5, "eval")

# ---------------------------------------------------------------- step 6 plan
# The coverage check of the last planning run (not part of the plan itself).
PLAN_COVERAGE_PATH = step_path(STEP_6, "coverage_report.json")

# ---------------------------------------------------------------- step 9 site
# The published site lives in the repository, not under data_dir: step 9 owns ``docs/`` and
# writes ``mkdocs.yml``; ``docs/wiki_index.json`` is the MCP index (contract C11).
DOCS_DIR = PROJECT_ROOT / section("site")["docs_dir"]
MKDOCS_PATH = PROJECT_ROOT / section("site")["mkdocs_file"]
WIKI_INDEX_FILE = section("mcp")["index_file"]

# --------------------------------------------------------------- step 10 update
# Contract C13. Per update ``<stamp>``, all in ``UPDATES_DIR`` (private, like the export):
# ``<stamp>_<export_filename>`` (the full export), ``<stamp>_new_messages.json`` (what was not
# seen before), ``<stamp>_report.json`` (config, commit, fingerprint, each step's summary) and
# ``<stamp>_update.log``.
UPDATE_STATE_PATH = step_path(STEP_10, "state.json")
# Held (``flock``) by a running update; names the step it runs (``utils.step_lease``).
UPDATE_LEASE_PATH = step_path(STEP_10, "running.json")
NEW_MESSAGES_SUFFIX = "new_messages.json"
UPDATE_REPORT_SUFFIX = "report.json"
UPDATE_LOG_SUFFIX = "update.log"

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
