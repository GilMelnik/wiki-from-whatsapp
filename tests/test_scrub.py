"""Tests for wiki_build.scrub."""

from __future__ import annotations

import pytest

from step_3_extract.scrub import REDACTION_MARK, scrub_claims, scrub_text
from step_4_entities.records import claim_contacts, find_phones


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
    assert restore_scrubbed_text(scrubbed, redactions, only={1}) == (
        f"התקשרו ל-{REDACTION_MARK} או a@b.com."
    )


def test_redactions_follow_text_order_across_patterns() -> None:
    """Emails are matched before phones, yet each value must go back to its own mark."""
    from step_3_extract.scrub import restore_scrubbed_text

    text = "חייגו 050-1234567, כתבו ל-foo@bar.com או למוקד *5400."
    result = scrub_text(text)
    assert [item["type"] for item in result.redactions] == ["phone", "email", "phone"]
    assert restore_scrubbed_text(result.text, result.redactions) == text



# ------------------------------------------------ dates and prices are not phone numbers
@pytest.mark.parametrize(
    "text",
    [
        "עד 22.6.2023 נרשמו עשרה זוגות.",
        "המענק עומד על 16,954 ש\"ח (החל ב-1.10.2023).",
        "ב-25.2.2025 בשעה 20:00 יתקיים וובינר.",
        "הכנס יתקיים ביום חמישי 04.06.2026.",
        "ההרשמה פתוחה מ-1.10.2023-5.10.2023 בלבד.",
        "הכנס יתקיים בתאריכים 10-12.6.2024.",
        "התקבל ב-2024-06-22 ונחתם ב-22-6-2024.",
        "בשנים 2023-2024 המחירים עלו.",
        "הוצאות בית משפט עשויות להגיע ל-1500-2000$ בנוסף לשכר הטרחה.",
        "הצעות המחיר הן 1800-2000 ש\"ח.",
        "האבחון עולה 4,000-5,000 ש\"ח, ולא 1800-2000 ש״ח.",
        "התהליך כולו עולה $150000-200000.",
        "עלות של 1.500.000 ₪ בסך הכול.",
    ],
)
def test_dates_and_prices_are_left_alone(text: str) -> None:
    result = scrub_text(text)
    assert result.text == text
    assert result.redactions == []
    assert find_phones(text) == []


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("ניתן להתקשר ל-050-1234567 לתיאום.", "050-1234567"),
        ("ההרשמה בטלפון 03-6790217.", "03-6790217"),
        ("אפשר לפנות בטלפון 039771001 או במייל.", "039771001"),
        ("המספר הוא +972-50-123-4567.", "+972-50-123-4567"),
        ("המספר האמריקאי הוא 1-800-532-1987.", "1-800-532-1987"),
        ("צריך להתקשר (+1 (800) 772-1213) ולקבוע תור.", "+1 (800) 772-1213"),
        # a phone number next to an amount or a date is still a phone number
        ("העברתי ל-0501234567 100 ש\"ח.", "0501234567 100"),
        ("נקבע ל-25.2.2025 0501234567.", "25.2.2025 0501234567"),
        # a range without a currency mark, an impossible date, a row of zeros: a person decides
        ("בין 1500-2000 משתתפים.", "1500-2000"),
        ("נרשם 32.13.2023 בטופס.", "32.13.2023"),
        ("ממלאים אפסים בלבד (000-00-000).", "000-00-000"),
        ("המספר 054-12.3.2021 נמסר.", "054-12.3.2021"),
    ],
)
def test_phone_numbers_are_still_redacted(text: str, value: str) -> None:
    result = scrub_text(text)
    assert result.redactions == [{"type": "phone", "value": value}]
    assert value not in result.text
    assert find_phones(text) == [value]


def test_a_date_next_to_a_phone_number_and_an_email_keeps_the_order() -> None:
    text = "ב-1.10.2023 כתבו אל foo@bar.com או חייגו 050-1234567 עד 5.10.2023."
    result = scrub_text(text)
    assert [item["value"] for item in result.redactions] == ["foo@bar.com", "050-1234567"]
    assert result.text == f"ב-1.10.2023 כתבו אל {REDACTION_MARK} או חייגו {REDACTION_MARK} עד 5.10.2023."


def test_contacts_skip_dates_and_prices_an_earlier_scrub_removed() -> None:
    """Claims scrubbed before the rule existed list dates and prices as removed phone numbers."""
    claim = {
        "claim_text": f"החל ב-{REDACTION_MARK} ההוצאות הן {REDACTION_MARK}$; לפרטים {REDACTION_MARK}.",
        "_redactions": [
            {"type": "phone", "value": "1.10.2023"},
            {"type": "phone", "value": "1500-2000"},
            {"type": "phone", "value": "03-6790217"},
        ],
    }
    _emails, phones, _sites = claim_contacts(claim)
    assert phones == ["03-6790217"]
    # a value whose mark is gone from the text is read on its own
    loose = {"claim_text": "הטקסט נכתב מחדש.", "_redactions": claim["_redactions"]}
    assert claim_contacts(loose)[1] == ["03-6790217", "1500-2000"]
