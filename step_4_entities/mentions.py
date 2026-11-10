"""Word-aware entity mention matching backed by dictabert-joint morphology.

The claim text is analyzed once into per-word records (prefix-stripped ``core``
surface + POS), cached to disk, and reused for both attribution (which claims
mention an entity) and highlighting. Matching is whole-word on the cores, so a
short name never matches inside a longer word, and Hebrew proclitics are stripped
by the model rather than guessed by a regex.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from step_4_entities.normalize import _is_hebrew, normalize_token
from utils.config import ENTITIES_CONFIG
from utils.json_io import write_json_file
from utils.paths import ENTITY_ANALYSIS_PATH

Word = dict[str, Any]

# A surface match is rejected only when every matched word is one of these clearly
# non-entity parts of speech (built once from config for O(1) membership checks).
_DISALLOWED_ENTITY_POS = frozenset(ENTITIES_CONFIG["disallowed_entity_pos"])
_ANALYZER_CONFIG = ENTITIES_CONFIG["analyzer"]


@runtime_checkable
class Analyzer(Protocol):
    """Turns texts into per-word records ``{token, core, prefixes, pos, lemma}``."""

    model_name: str

    def analyze_batch(self, texts: Sequence[str]) -> list[list[Word]]: ...


class SimpleAnalyzer:
    """Model-free fallback: whitespace tokens, no proclitic stripping, no POS.

    Still fixes the substring over-match (matching is whole-word), it just cannot
    strip Hebrew proclitics or apply the POS gate. Used for tests and when the
    model cannot be loaded.
    """

    model_name = "simple"

    def analyze_batch(self, texts: Sequence[str]) -> list[list[Word]]:
        return [
            [
                {"token": tok, "core": tok, "prefixes": [], "pos": None, "lemma": None}
                for tok in (text or "").split()
            ]
            for text in texts
        ]


class DictaAnalyzer:
    """Production analyzer wrapping ``HebrewTokenizer`` (dictabert-joint).

    Model, batch size, device and the free-GPU-memory floor come from ``entities.analyzer``
    in ``config.json``; the model only loads on first use (a cache miss).
    """

    def __init__(self, tokenizer: Any = None) -> None:
        if tokenizer is None:
            from step_4_entities.hebrew_tokenizer import HebrewTokenizer

            tokenizer = HebrewTokenizer(
                model_name=_ANALYZER_CONFIG["model"],
                batch_size=_ANALYZER_CONFIG["batch_size"],
                device=_ANALYZER_CONFIG["device"],
                min_free_gpu_mb=_ANALYZER_CONFIG["min_free_gpu_mb"],
            )
        self._tokenizer = tokenizer

    @property
    def model_name(self) -> str:
        return self._tokenizer.model_name

    def analyze_batch(self, texts: Sequence[str]) -> list[list[Word]]:
        return self._tokenizer.analyze_batch(list(texts))


def _attach_offsets(text: str, words: list[Word]) -> list[Word]:
    """Add ``start``/``end`` (char span of the prefix-stripped core) to each word.

    Forward-scans the original text for each token surface so highlighting lands
    on the entity word and not its attached proclitic. Best-effort: a token the
    scan cannot locate gets ``start``/``end`` = ``None``.
    """

    cursor = 0
    for word in words:
        token = word.get("token") or ""
        core = word.get("core") or token
        match = re.compile(re.escape(token), re.IGNORECASE).search(text, cursor)
        if match is None:
            match = re.compile(re.escape(core), re.IGNORECASE).search(text, cursor)
            if match is None:
                word["start"] = word["end"] = None
                continue
            word["start"], word["end"] = match.start(), match.end()
            cursor = match.end()
            continue
        # The core is the trailing part of the token (prefixes are proclitic).
        prefix_len = max(0, len(token) - len(core))
        word["start"] = match.start() + prefix_len
        word["end"] = match.end()
        cursor = match.end()
    return words


def name_words(name: str) -> list[str]:
    """The normalized words of an entity name, as ``find_mentions`` matches them."""

    return [t for t in (normalize_token(w) for w in name.split()) if t]


def _pos_ok(window: list[Word]) -> bool:
    # Reject only when every matched word is a clearly non-entity POS. ``None`` (no
    # POS, e.g. SimpleAnalyzer) is never disallowed, so the gate is a no-op there.
    return any(word.get("pos") not in _DISALLOWED_ENTITY_POS for word in window)


def _name_like(word: Word) -> bool:
    return word.get("ner") is not None or word.get("pos") == "PROPN"


def _window_ok(window: list[Word], *, single_hebrew: bool) -> bool:
    if not _pos_ok(window):
        return False
    # A single common Hebrew word (e.g. "כלל") collides with idioms like "בדרך כלל".
    # When POS/NER is available, demand name-like evidence (PROPN or a NER tag) so the
    # bare common-noun use is not attributed to the entity. Multi-word phrases and the
    # model-free path (pos is None) are exempt.
    if single_hebrew:
        word = window[0]
        if word.get("pos") is not None and not _name_like(word):
            return False
    return True


def find_mentions(words: list[Word], name: str) -> list[list[int]]:
    """Char spans where ``name`` appears as a whole-word run of cores in ``words``."""

    wanted = name_words(name)
    if not wanted or not words:
        return []
    cores = [normalize_token(word.get("core") or "") for word in words]
    span_len = len(wanted)
    single_hebrew = span_len == 1 and _is_hebrew(name)
    spans: list[list[int]] = []
    for i in range(len(words) - span_len + 1):
        if cores[i : i + span_len] != wanted:
            continue
        window = words[i : i + span_len]
        if not _window_ok(window, single_hebrew=single_hebrew):
            continue
        start, end = window[0].get("start"), window[-1].get("end")
        if start is None or end is None:
            continue
        spans.append([start, end])
    return spans


def analyze_claims(
    claims: list[dict[str, Any]], analyzer: Analyzer
) -> dict[str, list[Word]]:
    texts = [c.get("claim_text") or "" for c in claims]
    analyzed = analyzer.analyze_batch(texts)
    out: dict[str, list[Word]] = {}
    for claim, words in zip(claims, analyzed):
        claim_id = claim.get("claim_id")
        if not claim_id:
            continue
        out[claim_id] = _attach_offsets(claim.get("claim_text") or "", words)
    return out


def _text_fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[: _ANALYZER_CONFIG["fingerprint_chars"]]


def read_analysis_cache(
    cache_path: Path | str, model: str | None = None
) -> tuple[dict[str, list[Word]], dict[str, str]]:
    """The cached ``{claim_id: words}`` and text fingerprints (empty when unreadable or, with
    ``model``, when the cache was built by another model)."""

    cache = Path(cache_path)
    if not cache.is_file():
        return {}, {}
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, {}
    if model is not None and data.get("metadata", {}).get("model") != model:
        return {}, {}
    return data.get("analysis") or {}, data.get("fingerprints") or {}


def split_cached(
    claims: list[dict[str, Any]], cached: dict[str, list[Word]], fingerprints: dict[str, str]
) -> tuple[dict[str, list[Word]], list[dict[str, Any]]]:
    """Cache entries still valid for ``claims`` (same text), and the claims without one."""

    analysis: dict[str, list[Word]] = {}
    missing: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = claim.get("claim_id")
        if not claim_id:
            continue
        valid = fingerprints.get(claim_id) == _text_fingerprint(claim.get("claim_text") or "")
        if valid and claim_id in cached:
            analysis[claim_id] = cached[claim_id]
        else:
            missing.append(claim)
    return analysis, missing


def build_or_load_analysis(
    claims: list[dict[str, Any]],
    analyzer: Analyzer,
    *,
    cache_path: Path | str = ENTITY_ANALYSIS_PATH,
    source_path: Path | str | None = None,
) -> dict[str, list[Word]]:
    """Per-claim analysis, reusing the cache entry of every claim whose text is unchanged.

    Only new or edited claim texts are analyzed (the cache is per claim, keyed by a text
    fingerprint and the model name), so an incremental run analyzes only its new claims.
    The cache is rewritten, pruned to ``claims``, whenever something was added or dropped.
    """

    cached, fingerprints = read_analysis_cache(cache_path, analyzer.model_name)
    analysis, missing = split_cached(claims, cached, fingerprints)
    analysis.update(analyze_claims(missing, analyzer) if missing else {})
    if missing or set(cached) != set(analysis):
        write_json_file(
            {
                "metadata": {
                    "model": analyzer.model_name,
                    "source": str(source_path) if source_path is not None else None,
                    "claim_count": len(analysis),
                },
                "fingerprints": {
                    c["claim_id"]: _text_fingerprint(c.get("claim_text") or "")
                    for c in claims
                    if c.get("claim_id")
                },
                "analysis": analysis,
            },
            Path(cache_path),
        )
    return analysis


class MentionIndex:
    """Find which analyzed claims mention a name, without scanning every claim.

    Claims are indexed by the normalized core of each word; a name is only matched (with
    ``find_mentions``) against claims containing its first word.
    """

    def __init__(self, analysis: dict[str, list[Word]]) -> None:
        self._analysis = analysis
        self._by_core: dict[str, set[str]] = {}
        for claim_id, words in analysis.items():
            for word in words:
                core = normalize_token(word.get("core") or "")
                if core:
                    self._by_core.setdefault(core, set()).add(claim_id)

    def claims_mentioning(self, name: str) -> set[str]:
        wanted = name_words(name)
        if not wanted:
            return set()
        candidates = self._by_core.get(wanted[0], set())
        return {c for c in candidates if find_mentions(self._analysis[c], name)}
