"""Thresholds, signal vocabulary, and default paths for entity clustering."""

from pathlib import Path

DEFAULT_OUTPUT_PATH = Path("data/entities.json")
DEFAULT_ENTITY_DISTANCE_MATRIX_PATH = Path("data/entity_distance_matrix.npy")
DEFAULT_ENTITY_DISTANCE_META_PATH = Path("data/entity_distance_matrix.json")
# Bump when ``EntityPairIndex`` / ``transliteration_skeleton`` / ``normalize_name``
# logic changes (invalidates the cached distance matrix).
DISTANCE_METHOD = "string_plus_transliteration_v2"

# Similarity / clustering defaults. ponytail: hand-tuned heuristic thresholds,
# not learned. The human reviewer is the safety net, so they lean conservative
# (precision over recall). Bump SIMILARITY_THRESHOLD down to suggest more merges.
SIMILARITY_THRESHOLD = 0.88
MIN_SKELETON_LEN = 3  # shorter cross-script skeletons coincide too often
SAMPLE_CLAIMS_PER_MEMBER = 12

# Multi-signal clustering knobs (all hand-tuned, precision-first).
PREFIX_SIMILARITY = 0.93  # boost when one name is a whole-word prefix of the other
TOPIC_GUARD_CAP = 0.5  # cap for short near-identical names with disjoint topics
SHORT_NAME_MAX_CHARS = 8  # "short" names are the ones prone to homonym collisions
DEFAULT_SEED_PATH = Path("data/entities_seed.json")

# merge_signals vocabulary surfaced to the reviewer UI.
SIGNAL_CO_OCCUR = "co_occur"
SIGNAL_CONFIDENT_CONTACT = "confident_contact"
SIGNAL_SEED = "seed"
SIGNAL_PREFIX = "prefix"
SIGNAL_STRING = "string"
SIGNAL_TRANSLITERATION = "transliteration"
