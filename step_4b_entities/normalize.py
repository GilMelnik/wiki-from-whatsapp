"""Name normalization and cross-script transliteration helpers."""

from __future__ import annotations

import re
import unicodedata

from step_4b_entities.constants import MIN_SKELETON_LEN, SHORT_NAME_MAX_CHARS

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

# Honorifics/titles stripped from the *start* of a cleaned name so e.g.
# ``עו"ד הראל`` and ``הראל`` normalize alike. Matched after punctuation removal,
# so ``עו"ד`` has already become ``עו ד``.
_TITLE_PREFIX_RE = re.compile(
    r"^(?:עו ד|עורך דין|עורכת דין|adv|attorney|lawyer|dr|prof|mr|mrs|ms)\s+"
)


def normalize_name(name: str) -> str:
    """Casefold + strip niqqud/punctuation/titles + normalize Hebrew finals.

    Phone-like strings (no letters, 9-15 digits) collapse to a digits-only key so
    spacing/formatting variants of the same number align.
    """

    digits = re.sub(r"\D", "", name)
    if 9 <= len(digits) <= 15 and not any(ch.isalpha() for ch in name):
        return digits

    decomposed = unicodedata.normalize("NFKD", name)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = no_marks.casefold().translate(_HEB_FINALS)
    cleaned = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", lowered)).strip()
    stripped = _TITLE_PREFIX_RE.sub("", cleaned, count=1).strip()
    return stripped or cleaned


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


def _is_word_prefix(a: str, b: str) -> bool:
    """True when the shorter name is a whole-word prefix of the longer (>=3 chars).

    Catches ``הראל`` -> ``הראל ברק`` and ``עו"ד הראל`` (normalized to ``הראל``)
    without treating arbitrary substrings as matches.
    """

    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 3 or short == long:
        return False
    return long.startswith(short + " ")


def _is_short_name(norm: str) -> bool:
    return len(norm) <= SHORT_NAME_MAX_CHARS
