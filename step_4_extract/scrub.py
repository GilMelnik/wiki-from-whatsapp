"""Deterministic PII scrub safety net.

Auto-redacts only high-confidence contact PII that should never appear in wiki
content: phone numbers and email addresses. Member and provider names are left
intact; anonymity is enforced by the extraction prompt and human draft review.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

REDACTION_MARK = "[הוסר]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A run that contains 9-15 digits once separators are stripped (phone-like).
_PHONE_RE = re.compile(r"(?<![\w])\+?[\d][\d\s().\-]{7,}\d(?![\w])")

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

# Shared prompt directive so every LLM stage avoids producing the term at all.
FORBIDDEN_TERM_INSTRUCTION = (
    'אסור בתכלית האיסור להשתמש בצירוף "אמא פונדקאית" / "אם פונדקאית" / '
    '"האמא הפונדקאית" / "אמהות פונדקאיות" או כל הטיה אחרת המתארת את הפונדקאית '
    'כ"אם" או כ"אמא" — זהו מונח שגוי ופוגעני. השתמש אך ורק ב"פונדקאית" '
    '(או "נושאת").'
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


def scrub_text(text: str) -> ScrubResult:
    if not text:
        return ScrubResult(text="")

    redactions: list[dict[str, str]] = []
    result = text

    def _redact(pattern: re.Pattern[str], kind: str) -> None:
        nonlocal result

        def repl(match: re.Match[str]) -> str:
            value = match.group(0)
            redactions.append({"type": kind, "value": value})
            return REDACTION_MARK

        result = pattern.sub(repl, result)

    _redact(_EMAIL_RE, "email")
    _redact(_PHONE_RE, "phone")

    return ScrubResult(text=result, redactions=redactions)


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


def summarize_redactions(redactions: list[dict[str, str]]) -> str:
    """Human-readable summary, e.g. ``phone ×2, email ×1``."""

    counts = Counter(item["type"] for item in redactions)
    return ", ".join(f"{kind} ×{count}" for kind, count in sorted(counts.items()))


def restore_scrubbed_text(text: str, redactions: list[dict[str, str]]) -> str:
    """Replace redaction marks with the original values, in scrub order."""

    restored = text
    for item in redactions:
        restored = restored.replace(REDACTION_MARK, item["value"], 1)
    return restored
