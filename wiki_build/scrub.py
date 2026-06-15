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
        claim["claim_text"] = scrubbed.text
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
