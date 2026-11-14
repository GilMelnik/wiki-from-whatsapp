"""Deterministic PII scrub safety net.

Auto-redacts only high-confidence contact PII that should never appear in wiki
content: phone numbers and email addresses (the patterns of ``utils.contact_patterns``).
Member and provider names are left intact; anonymity is enforced by the extraction prompt
and human draft review. The prompt directive against the forbidden term is
``utils.forbidden_term``; ``correct_surrogate_terminology`` is its safety net here.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from utils.contact_patterns import EMAIL_RE, PHONE_RE, SHORT_PHONE_RE

REDACTION_MARK = "[הוסר]"
# While scrubbing, each match is replaced by one supplementary private-use character (never
# a word character, digit or separator, so later patterns see it like the mark) numbered
# from here; chat text does not contain these code points.
_PLACEHOLDER_BASE = 0xF0000
_PLACEHOLDER_RE = re.compile("[\U000F0000-\U000FFFFD]")

# Forbidden, offensive framing of the surrogate as the child's "mother". The
# surrogate is never the mother of the child; collapse the phrase to the correct
# term, keeping the definite article / plural of the surrogate word itself.
#
# Two patterns, deliberately conservative to avoid corrupting the very common
# Hebrew word "אם" meaning *if* (e.g. "אם הפונדקאית חתמה" = "if the surrogate
# signed"):
#   1. Unambiguous mother nouns (אמא / אימא / אמהות / אימהות), with an optional
#      proclitic and definite article: e.g. "אמא פונדקאית", "האמא הפונדקאית",
#      "לאמא הפונדקאית", "אמהות פונדקאיות".
#   2. "אם" ONLY when it carries a preposition (ל/מ/כ) that the conjunction
#      "if" can never take: e.g. "לאם הפונדקאית". Bare "אם", "האם", "ואם",
#      "באם" are left untouched (handled by the prompt + human review).
_MOTHER_NOUN_RE = re.compile(
    r"(?P<pre>[למכבוש]?)ה?(?:אמא|אימא|אמהות|אימהות)[\s\u05be\-]+"
    r"(?P<sur>ה?פונדקאי(?:ות|ת))"
)
_MOTHER_EM_RE = re.compile(
    r"(?P<pre>[למכ])אם[\s\u05be\-]+(?P<sur>ה?פונדקאי(?:ות|ת))"
)


def _collapse_to_surrogate(match: re.Match[str]) -> str:
    """Drop the "mother" word, keeping the proclitic and the surrogate word.

    Re-attaches a leading preposition, contracting ל/ב/כ + ה (e.g.
    "לאם הפונדקאית" -> "לפונדקאית", "מהאמא הפונדקאיות" -> "מהפונדקאיות").
    """

    pre = match.group("pre")
    sur = match.group("sur")
    if pre in ("ל", "ב", "כ") and sur.startswith("ה"):
        sur = sur[1:]
    return f"{pre}{sur}"


def correct_surrogate_terminology(text: str) -> str:
    """Strip the offensive "surrogate mother" phrasing from text.

    Replaces variants such as "אמא פונדקאית", "האמא הפונדקאית",
    "אמהות פונדקאיות" and "לאם הפונדקאית" with the correct term, preserving the
    surrogate word's definite article and number. Acts as a deterministic safety
    net behind the prompt instruction above. Intentionally does not touch bare
    "אם"/"האם" to avoid mangling the conjunction "if".
    """

    if not text:
        return text
    text = _MOTHER_NOUN_RE.sub(_collapse_to_surrogate, text)
    text = _MOTHER_EM_RE.sub(_collapse_to_surrogate, text)
    return text


@dataclass
class ScrubResult:
    text: str
    redactions: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.redactions)


def _placeholder(index: int) -> str:
    return chr(_PLACEHOLDER_BASE + index)


def scrub_text(text: str) -> ScrubResult:
    """Replace emails and phone numbers with ``REDACTION_MARK``.

    ``redactions`` lists one entry per mark, in the order the marks appear in the text, so
    ``restore_scrubbed_text`` puts every value back in its own place. (The patterns run one
    after the other; each match is first replaced by a placeholder that the later patterns
    treat exactly like the mark, then the placeholders are numbered in text order.)
    """
    if not text:
        return ScrubResult(text="")

    found: list[dict[str, str]] = []
    result = text

    def _redact(pattern: re.Pattern[str], kind: str) -> None:
        nonlocal result

        def repl(match: re.Match[str]) -> str:
            found.append({"type": kind, "value": match.group(0)})
            return _placeholder(len(found) - 1)

        result = pattern.sub(repl, result)

    _redact(EMAIL_RE, "email")
    _redact(PHONE_RE, "phone")
    _redact(SHORT_PHONE_RE, "phone")

    in_text_order = [
        found[ord(match.group(0)) - _PLACEHOLDER_BASE]
        for match in _PLACEHOLDER_RE.finditer(result)
    ]
    return ScrubResult(
        text=_PLACEHOLDER_RE.sub(REDACTION_MARK, result), redactions=in_text_order
    )


def scrub_claims(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Scrub ``claim_text`` in place; return a summary of what was changed."""

    total_redactions = 0
    pii_review_claims = 0
    for claim in claims:
        original = claim.get("claim_text", "")
        scrubbed = scrub_text(original)
        claim["claim_text"] = correct_surrogate_terminology(scrubbed.text)
        if scrubbed.redactions:
            claim["_redactions"] = scrubbed.redactions
            total_redactions += len(scrubbed.redactions)
            pii_review_claims += 1
    return {
        "claims_count": len(claims),
        "total_redactions": total_redactions,
        "pii_review_claims": pii_review_claims,
    }


def restore_scrubbed_text(
    text: str, redactions: list[dict[str, str]], only: Collection[int] | None = None
) -> str:
    """Put redacted values back: mark number ``i`` (in text order) gets ``redactions[i]``.

    With ``only``, just the marks with those numbers are restored; the others stay. Marks
    without a matching redaction are left as they are.
    """

    parts = text.split(REDACTION_MARK)
    pieces = [parts[0]]
    for index, part in enumerate(parts[1:]):
        restore = index < len(redactions) and (only is None or index in only)
        pieces.append(redactions[index]["value"] if restore else REDACTION_MARK)
        pieces.append(part)
    return "".join(pieces)
