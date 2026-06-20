"""Step 4b: resolve free-text entity strings into a global canonical registry.

Claims carry free-text ``entities`` (hundreds of distinct strings) where the
same real entity shows up as many variants (``תמוז`` / ``tamuz`` / ``Tammuz``,
``כללית`` / ``קופת חולים כללית``, ``David Shield`` / ``DavidShield``). This step
clusters those strings into suggested canonical entities so that step 5 stops
fragmenting per-entity stances.

Similarity is deterministic and high-precision: a normalized string-similarity
signal (catches same-script spelling variants) plus a cross-script
transliteration signal (a Hebrew->Latin consonant skeleton, which catches
``דייויד שילד`` <-> ``David Shield``, ``עמית פלס`` <-> ``Amit Peles``). The human
merges whatever is left in the review tool.

ponytail: semantic sentence embeddings were measured and deliberately rejected
here. For short proper nouns multilingual-e5 encodes *category*, not identity, so
distinct same-type entities score higher than true transliterations
(``תל אביב``/``ירושלים`` = 0.96 but ``David Shield``/``דיוויד שילד`` = 0.90) — there
is no usable threshold. Taxonomy ``keywords`` are NOT used as a merge signal for
the same reason: a page like ``providers-other`` lists many distinct agencies
that share a topic but are not the same entity.

Output: ``data/entities.json`` (suggested clusters; the reviewer writes
``data/entities_edited.json``). Contact details (emails/phones from the PII
redaction trail, websites from claim text) are preserved per entity.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from step_4_extract.scrub import find_emails, find_phones
from utils.json_io import write_json_file
from utils.paths import ORIGINAL_CLAIMS_PATH, resolve_claims_path, resolve_entities_path

DEFAULT_OUTPUT_PATH = Path("data/entities.json")
DEFAULT_ENTITY_DISTANCE_MATRIX_PATH = Path("data/entity_distance_matrix.npy")
DEFAULT_ENTITY_DISTANCE_META_PATH = Path("data/entity_distance_matrix.json")
# Bump when ``_pair_similarity`` / ``transliteration_skeleton`` logic changes.
DISTANCE_METHOD = "string_plus_transliteration_v1"

# Similarity / clustering defaults. ponytail: hand-tuned heuristic thresholds,
# not learned. The human reviewer is the safety net, so they lean conservative
# (precision over recall). Bump SIMILARITY_THRESHOLD down to suggest more merges.
SIMILARITY_THRESHOLD = 0.88
MIN_SKELETON_LEN = 3  # shorter cross-script skeletons coincide too often
SAMPLE_CLAIMS_PER_MEMBER = 12

# Hebrew consonants -> Latin skeleton phonemes (vowels/matres lectionis dropped).
_HEB_TO_LATIN = {
    "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
    "ח": "h", "ט": "t", "י": "", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "", "פ": "p", "ף": "p",
    "צ": "c", "ץ": "c", "ק": "k", "ר": "r", "ש": "s", "ת": "t",
}
_LATIN_VOWELS = set("aeiou")

_HEB_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_PUNCT_RE = re.compile(r"[\"'״׳`.,()\[\]/\-–—_:;|]+")
_WS_RE = re.compile(r"\s+")

_URL_RE = re.compile(
    r"https?://[^\s)\]<>\"']+"
    r"|\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|gov|edu|io|info|biz|travel|co\.il|co|il)\b"
)


def normalize_name(name: str) -> str:
    """Casefold + strip niqqud/punctuation + normalize Hebrew finals for matching."""

    decomposed = unicodedata.normalize("NFKD", name)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = no_marks.casefold().translate(_HEB_FINALS)
    cleaned = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", cleaned).strip()


def claim_mentions_name(claim: dict[str, Any], name: str) -> bool:
    """True when ``name`` appears in ``claim_text`` (case-insensitive)."""

    if not name.strip():
        return False
    text = claim.get("claim_text") or ""
    return name.casefold() in text.casefold()


def _extract_websites(text: str) -> list[str]:
    return [m.group(0).rstrip(".") for m in _URL_RE.finditer(text or "")]


def _claim_contacts(
    claim: dict[str, Any],
    original: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Emails, phones, websites from a claim and its pipeline-original trail."""

    emails: set[str] = set()
    phones: set[str] = set()
    websites: set[str] = set()
    sources = [claim]
    if original is not None:
        sources.append(original)
    for src in sources:
        text = src.get("claim_text", "")
        for red in src.get("_redactions") or []:
            if red.get("type") == "email":
                emails.add(red["value"])
            elif red.get("type") == "phone":
                phones.add(red["value"])
        emails.update(find_emails(text))
        phones.update(find_phones(text))
        websites.update(_extract_websites(text))
    return sorted(emails), sorted(phones), sorted(websites)


def load_claims_for_entities(
    claims_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], Path, dict[str, dict[str, Any]] | None]:
    """Load claims for entity collection, with original contact trail when edited."""

    resolved = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved.open(encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    original_by_id: dict[str, dict[str, Any]] | None = None
    if (
        resolved.resolve() != ORIGINAL_CLAIMS_PATH.resolve()
        and ORIGINAL_CLAIMS_PATH.is_file()
    ):
        with ORIGINAL_CLAIMS_PATH.open(encoding="utf-8") as f:
            original_by_id = {
                c["claim_id"]: c for c in json.load(f).get("claims") or []
            }
    return claims, resolved, original_by_id


def collect_entities(
    claims: list[dict[str, Any]],
    *,
    original_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Distinct entity strings with counts, sample claims, topics, contacts."""

    tag_counts: Counter[str] = Counter()
    claim_ids: dict[str, set[str]] = defaultdict(set)
    sample_ids: dict[str, list[str]] = defaultdict(list)
    topics: dict[str, set[str]] = defaultdict(set)
    emails: dict[str, set[str]] = defaultdict(set)
    phones: dict[str, set[str]] = defaultdict(set)
    websites: dict[str, set[str]] = defaultdict(set)

    def _absorb_claim(name: str, claim: dict[str, Any], original: dict[str, Any] | None) -> None:
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in claim_ids[name]:
            return
        claim_ids[name].add(claim_id)
        if len(sample_ids[name]) < SAMPLE_CLAIMS_PER_MEMBER:
            sample_ids[name].append(claim_id)
        topics[name].update(claim.get("topic_tags") or [])
        claim_emails, claim_phones, claim_sites = _claim_contacts(claim, original)
        emails[name].update(claim_emails)
        phones[name].update(claim_phones)
        websites[name].update(claim_sites)

    for claim in claims:
        claim_id = claim.get("claim_id")
        original = (original_by_id or {}).get(claim_id) if claim_id else None
        for name in claim.get("entities") or []:
            if not isinstance(name, str) or not name.strip():
                continue
            tag_counts[name] += 1
            _absorb_claim(name, claim, original)

    for name in tag_counts:
        for claim in claims:
            claim_id = claim.get("claim_id")
            original = (original_by_id or {}).get(claim_id) if claim_id else None
            if claim_mentions_name(claim, name):
                _absorb_claim(name, claim, original)

    entities: list[dict[str, Any]] = []
    for name in tag_counts:
        ids = sorted(claim_ids[name])
        entities.append(
            {
                "name": name,
                "normalized": normalize_name(name),
                "count": len(ids),
                "claim_ids": ids,
                "sample_claim_ids": sample_ids[name],
                "topics": sorted(topics[name]),
                "contacts": {
                    "email": sorted(emails[name]),
                    "phone": sorted(phones[name]),
                    "website": sorted(websites[name]),
                },
            }
        )
    entities.sort(key=lambda e: e["count"], reverse=True)
    return entities


def _is_hebrew(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    hebrew = sum(1 for c in letters if "\u0590" <= c <= "\u05ff")
    return hebrew >= len(letters) / 2


def transliteration_skeleton(name: str) -> str:
    """Vowel-free consonant skeleton, comparable across Hebrew and Latin."""

    decomposed = unicodedata.normalize("NFKD", name)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    out: list[str] = []
    for ch in no_marks.lower():
        if ch in _HEB_TO_LATIN:
            out.append(_HEB_TO_LATIN[ch])
        elif ch == "w":
            out.append("v")
        elif ch.isalpha() and ch not in _LATIN_VOWELS:
            out.append(ch)
    return "".join(out)


def _pair_similarity(
    i: int,
    j: int,
    *,
    norms: list[str],
    raw: list[str],
    skeletons: list[str],
) -> float:
    sim = SequenceMatcher(None, norms[i], norms[j]).ratio()
    # Cross-script transliteration only: a Hebrew vs Latin pair compared on the
    # consonant skeleton. Same-script pairs are already handled by string sim and
    # must not be re-merged here (that is how distinct same-type entities leak in).
    if _is_hebrew(raw[i]) != _is_hebrew(raw[j]):
        sa, sb = skeletons[i], skeletons[j]
        if min(len(sa), len(sb)) >= MIN_SKELETON_LEN:
            sim = max(sim, SequenceMatcher(None, sa, sb).ratio())
    return sim


def _distance_matrix(
    norms: list[str], raw: list[str], skeletons: list[str]
) -> np.ndarray:
    n = len(norms)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            d = 1.0 - _pair_similarity(
                i, j, norms=norms, raw=raw, skeletons=skeletons
            )
            dist[i, j] = dist[j, i] = d
    return dist


def _entity_distance_matrix_metadata(
    source_path: Path,
    names: list[str],
) -> dict[str, Any]:
    return {
        "source": str(source_path.resolve()),
        "entity_count": len(names),
        "entity_names": names,
        "distance_method": DISTANCE_METHOD,
        "min_skeleton_len": MIN_SKELETON_LEN,
    }


def _entity_distance_matrix_need_rebuild(
    meta_path: Path,
    matrix_path: Path,
    expected: dict[str, Any],
) -> bool:
    if not meta_path.exists() or not matrix_path.exists():
        return True
    with meta_path.open(encoding="utf-8") as f:
        stored = json.load(f).get("metadata", {})
    return stored != expected


def ensure_entity_distance_matrix(
    entities: list[dict[str, Any]],
    source_path: Path | str,
    *,
    matrix_path: Path | str = DEFAULT_ENTITY_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = DEFAULT_ENTITY_DISTANCE_META_PATH,
) -> np.ndarray:
    """Build or load the cached entity-name distance matrix."""

    names = [e["name"] for e in entities]
    norms = [e["normalized"] for e in entities]
    skeletons = [transliteration_skeleton(name) for name in names]

    source = Path(source_path).resolve()
    matrix_output = Path(matrix_path)
    meta_output = Path(meta_path)
    expected_meta = _entity_distance_matrix_metadata(source, names)

    if not _entity_distance_matrix_need_rebuild(
        meta_output, matrix_output, expected_meta
    ):
        return np.load(matrix_output)

    dist = _distance_matrix(norms, names, skeletons)
    matrix_output.parent.mkdir(parents=True, exist_ok=True)
    np.save(matrix_output, dist)
    write_json_file({"metadata": expected_meta}, meta_output)
    return dist


def _cluster_labels(dist: np.ndarray, *, similarity_threshold: float) -> list[int]:
    n = dist.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering

    labels = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=1.0 - similarity_threshold,
        metric="precomputed",
        linkage="complete",
    ).fit_predict(dist)
    return labels.tolist()


def _cluster_cohesion(members: list[int], dist: np.ndarray) -> float:
    """Worst (largest) pairwise distance turned into a similarity score."""

    if len(members) < 2:
        return 1.0
    diameter = max(dist[i, j] for i in members for j in members if i < j)
    return round(1.0 - float(diameter), 4)


def _union_contacts(members: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {"email": set(), "phone": set(), "website": set()}
    for member in members:
        for kind, values in (member.get("contacts") or {}).items():
            out.setdefault(kind, set()).update(values)
    return {kind: sorted(values) for kind, values in out.items()}


def build_entity(entity_id: str, member_entities: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble one canonical-entity record from its member name records."""

    members = sorted(member_entities, key=lambda e: e["count"], reverse=True)
    canonical_name = members[0]["name"]
    topics: set[str] = set()
    for member in members:
        topics.update(member.get("topics") or [])
    return {
        "entity_id": entity_id,
        "canonical_name": canonical_name,
        "status": "suggested",
        "aliases": [m["name"] for m in members],
        "members": [
            {
                "name": m["name"],
                "claim_ids": m.get("claim_ids"),
                "count": m["count"],
                "sample_claim_ids": m["sample_claim_ids"],
                "topics": m["topics"],
                "contacts": m["contacts"],
            }
            for m in members
        ],
        "contacts": _union_contacts(members),
        "topics": sorted(topics),
        "score": 1.0,
    }


def cluster_entities(
    entities: list[dict[str, Any]],
    source_path: Path | str,
    *,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    matrix_path: Path | str = DEFAULT_ENTITY_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = DEFAULT_ENTITY_DISTANCE_META_PATH,
) -> list[dict[str, Any]]:
    """Group distinct entity records into suggested canonical entities."""

    dist = ensure_entity_distance_matrix(
        entities,
        source_path,
        matrix_path=matrix_path,
        meta_path=meta_path,
    )
    labels = _cluster_labels(dist, similarity_threshold=similarity_threshold)

    grouped: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        grouped[label].append(idx)

    out_entities: list[dict[str, Any]] = []
    for n, (_, indices) in enumerate(sorted(grouped.items())):
        entity = build_entity(f"e{n:04d}", [entities[i] for i in indices])
        entity["score"] = _cluster_cohesion(indices, dist)
        out_entities.append(entity)
    out_entities.sort(
        key=lambda e: sum(m["count"] for m in e["members"]), reverse=True
    )
    return out_entities


def run(
    claims_path: Path | str | None = None,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    claims, resolved_claims, original_by_id = load_claims_for_entities(claims_path)

    entities = collect_entities(claims, original_by_id=original_by_id)
    out_entities = cluster_entities(
        entities,
        resolved_claims,
        similarity_threshold=similarity_threshold,
    )

    output = {
        "entities": out_entities,
        "metadata": {
            "source": str(resolved_claims),
            "distinct_entity_count": len(entities),
            "entity_count": len(out_entities),
            "multi_member_count": sum(1 for e in out_entities if len(e["members"]) > 1),
            "merge_method": "string_plus_transliteration",
            "similarity_threshold": similarity_threshold,
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


# --- Resolver: applied by step 5 aggregate to map raw entities to canonical ---


class EntityResolver:
    """Map a claim's raw entity strings to canonical entity names.

    Default mapping is by name. A member with an explicit ``claim_ids`` list
    creates per-claim overrides (used when a single string, e.g. a first name,
    was split across two real people in the review tool).
    """

    def __init__(self, entities: list[dict[str, Any]]):
        self._by_name: dict[str, str] = {}
        self._by_name_claim: dict[tuple[str, str], str] = {}
        self._registry: dict[str, dict[str, Any]] = {}
        for entity in entities:
            canonical = entity["canonical_name"]
            self._registry[canonical] = {
                "entity_id": entity.get("entity_id"),
                "aliases": entity.get("aliases") or [],
                "contacts": entity.get("contacts") or {},
                "topics": entity.get("topics") or [],
            }
            for member in entity.get("members") or []:
                name = member["name"]
                claim_ids = member.get("claim_ids")
                if claim_ids:
                    for claim_id in claim_ids:
                        self._by_name_claim[(name, claim_id)] = canonical
                else:
                    self._by_name[name] = canonical

    def canonical(self, name: str, claim_id: str | None = None) -> str:
        if claim_id is not None:
            override = self._by_name_claim.get((name, claim_id))
            if override is not None:
                return override
        return self._by_name.get(name, name)

    def resolve_claim(self, claim: dict[str, Any]) -> list[str]:
        claim_id = claim.get("claim_id")
        out: list[str] = []
        for name in claim.get("entities") or []:
            canonical = self.canonical(name, claim_id)
            if canonical not in out:
                out.append(canonical)
        return out

    def registry(self) -> dict[str, dict[str, Any]]:
        return self._registry


def load_entity_resolver(path: Path | str | None = None) -> EntityResolver | None:
    """Load the (edited) entity registry, or ``None`` when no file exists."""

    resolved = Path(path) if path is not None else resolve_entities_path()
    if not resolved.is_file():
        return None
    with resolved.open(encoding="utf-8") as f:
        return EntityResolver(json.load(f).get("entities") or [])


def apply_entity_resolution(
    claims: list[dict[str, Any]], resolver: EntityResolver | None
) -> None:
    """Rewrite each claim's ``entities`` to canonical names, in place."""

    if resolver is None:
        return
    for claim in claims:
        claim["entities"] = resolver.resolve_claim(claim)


if __name__ == "__main__":
    meta = run(similarity_threshold=0.80)
    print(
        f"{meta['entity_count']} entities "
        f"({meta['multi_member_count']} multi-member) "
        f"from {meta['distinct_entity_count']} distinct strings "
        f"via {meta['merge_method']}"
    )

    # Cleanup of the rejected embedding-cache experiment, if present.
    _stale = Path("data/entity_embeddings.json")
    if _stale.exists():
        _stale.unlink()
