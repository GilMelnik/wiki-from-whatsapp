"""Thresholds and signal vocabulary for entity clustering.

Artifact paths live in ``utils.paths`` (derived from the configurable data root).
"""

ENTITY_ANALYSIS_MODEL = "dicta-il/dictabert-joint"

# A surface match is rejected only when every matched word is one of these clearly
# non-entity parts of speech. NOUN/PROPN/NUM/ADJ/X pass (org names are often
# adjectival in form, e.g. "כללית", so ADJ is intentionally allowed).
DISALLOWED_ENTITY_POS = frozenset(
    {"VERB", "ADV", "ADP", "AUX", "CCONJ", "SCONJ", "DET", "PRON", "PART", "INTJ", "PUNCT"}
)
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

# merge_signals vocabulary surfaced to the reviewer UI.
SIGNAL_CO_OCCUR = "co_occur"
SIGNAL_CONFIDENT_CONTACT = "confident_contact"
SIGNAL_SEED = "seed"
SIGNAL_PREFIX = "prefix"
SIGNAL_STRING = "string"
SIGNAL_TRANSLITERATION = "transliteration"
