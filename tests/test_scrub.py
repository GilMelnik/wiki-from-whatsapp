"""Tests for wiki_build.scrub."""

from __future__ import annotations

from step_3_extract.scrub import REDACTION_MARK, scrub_claims, scrub_text


def test_hebrew_legal_phrases_unchanged() -> None:
    for text in (
        "מומלץ להיעזר בעורך דין לצורך תהליך האזרחות.",
        "פסק דין בבית המשפט קבע את ההורות.",
    ):
        result = scrub_text(text)
        assert result.text == text
        assert result.redactions == []


def test_provider_names_unchanged() -> None:
    text = "חברת הביטוח דויד שילד (David Shield) מעמידה אתר לרופאים."
    result = scrub_text(text)
    assert result.text == text
    assert result.redactions == []


def test_latin_phrase_unchanged() -> None:
    text = "ארגון MHB (Men Having Babies) מנהל את תוכנית GPAP."
    result = scrub_text(text)
    assert result.text == text
    assert result.redactions == []


def test_email_redacted() -> None:
    text = "ניתן לפנות לכתובת foo@bar.com לקבלת מידע."
    result = scrub_text(text)
    assert "foo@bar.com" not in result.text
    assert REDACTION_MARK in result.text
    assert result.redactions == [{"type": "email", "value": "foo@bar.com"}]


def test_phone_redacted() -> None:
    text = "ניתן להתקשר ל-050-1234567 לתיאום."
    result = scrub_text(text)
    assert "050-1234567" not in result.text
    assert REDACTION_MARK in result.text
    assert len(result.redactions) == 1
    assert result.redactions[0]["type"] == "phone"


def test_scrub_claims_attaches_metadata() -> None:
    claims = [
        {"claim_id": "c1", "claim_text": "צור קשר ב-050-1234567."},
        {"claim_id": "c2", "claim_text": "עורך דין מומלץ."},
    ]
    summary = scrub_claims(claims)
    assert summary["total_redactions"] == 1
    assert summary["pii_review_claims"] == 1
    assert "_redactions" in claims[0]
    assert "_redactions" not in claims[1]


def test_restore_scrubbed_text() -> None:
    from step_3_extract.scrub import restore_scrubbed_text

    scrubbed = f"התקשרו ל-{REDACTION_MARK} או {REDACTION_MARK}."
    redactions = [
        {"type": "phone", "value": "050-1234567"},
        {"type": "email", "value": "a@b.com"},
    ]
    restored = restore_scrubbed_text(scrubbed, redactions)
    assert restored == "התקשרו ל-050-1234567 או a@b.com."
