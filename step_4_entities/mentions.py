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

from step_4_entities.constants import (
    DEFAULT_ENTITY_ANALYSIS_PATH,
    DISALLOWED_ENTITY_POS,
)
from step_4_entities.normalize import _is_hebrew, normalize_token
from utils.json_io import write_json_file

Word = dict[str, Any]


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
    """Production analyzer wrapping the shared ``HebrewTokenizer`` (dictabert-joint).

    ponytail: a small batch keeps the lex head's vocab argsort from OOMing a modest
    GPU. If matching ever needs more throughput, set ``CUDA_VISIBLE_DEVICES=`` to
    run on CPU, or raise ``batch_size`` on a larger card.
    """

    def __init__(self, tokenizer: Any = None, **kwargs: Any) -> None:
        if tokenizer is None:
            from step_1_threads_split.tf_idf.hebrew_tokenizer import HebrewTokenizer

            kwargs.setdefault("batch_size", 8)
            tokenizer = HebrewTokenizer(**kwargs)
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


def _name_words(name: str) -> list[str]:
    return [t for t in (normalize_token(w) for w in name.split()) if t]


def _pos_ok(window: list[Word]) -> bool:
    # Reject only when every matched word is a clearly non-entity POS. ``None`` (no
    # POS, e.g. SimpleAnalyzer) is never disallowed, so the gate is a no-op there.
    return any(word.get("pos") not in DISALLOWED_ENTITY_POS for word in window)


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

    name_words = _name_words(name)
    if not name_words or not words:
        return []
    cores = [normalize_token(word.get("core") or "") for word in words]
    span_len = len(name_words)
    single_hebrew = span_len == 1 and _is_hebrew(name)
    spans: list[list[int]] = []
    for i in range(len(words) - span_len + 1):
        if cores[i : i + span_len] != name_words:
            continue
        window = words[i : i + span_len]
        if not _window_ok(window, single_hebrew=single_hebrew):
            continue
        start, end = window[0].get("start"), window[-1].get("end")
        if start is None or end is None:
            continue
        spans.append([start, end])
    return spans


def mentions_name(words: list[Word], name: str) -> bool:
    return bool(find_mentions(words, name))


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


def _signature(claims: list[dict[str, Any]], model_name: str) -> str:
    payload = sorted(
        [c.get("claim_id"), c.get("claim_text") or ""]
        for c in claims
        if c.get("claim_id")
    )
    blob = json.dumps([model_name, payload], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def build_or_load_analysis(
    claims: list[dict[str, Any]],
    analyzer: Analyzer,
    *,
    cache_path: Path | str = DEFAULT_ENTITY_ANALYSIS_PATH,
    source_path: Path | str | None = None,
) -> dict[str, list[Word]]:
    """Build the per-claim analysis or load it from ``cache_path`` when fresh."""

    cache = Path(cache_path)
    signature = _signature(claims, analyzer.model_name)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict) and data.get("metadata", {}).get("signature") == signature:
            return data["analysis"]

    analysis = analyze_claims(claims, analyzer)
    payload = {
        "metadata": {
            "signature": signature,
            "model": analyzer.model_name,
            "source": str(source_path) if source_path is not None else None,
            "claim_count": len(analysis),
        },
        "analysis": analysis,
    }
    write_json_file(payload, cache)
    return analysis
