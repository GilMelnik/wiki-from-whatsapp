"""Seed taxonomy for the surrogacy wiki.

Defines the canonical set of wiki pages (topics), their Hebrew titles, slugs,
hierarchy and the keywords used for heuristic tagging and the offline mock
LLM provider. The taxonomy is intentionally extensible: the LLM tagging step
may attach emergent topic ids that are not listed here, and those are collected
under the ``EMERGENT`` category during aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicPage:
    """A single wiki page in the taxonomy."""

    id: str
    title_he: str
    category: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    parent: str | None = None

    @property
    def slug(self) -> str:
        return self.id


# Top-level categories (used to build the site navigation).
CATEGORIES: dict[str, str] = {
    "start": "מבוא והתחלה",
    "geography": "לפי מדינה",
    "providers": "סוכנויות וספקים",
    "legal": "היבטים משפטיים",
    "religion": "גיור ויהדות",
    "money": "כסף תשלומים, המרות והעברות",
    "process": "שלבי התהליך",
    "medical": "רפואה ומרפאות",
    "emergent": "נושאים נוספים",
}


TAXONOMY: tuple[TopicPage, ...] = (
    # --- Getting started ---
    TopicPage(
        id="overview",
        title_he="סקירה כללית והתחלת תהליך",
        category="start",
        keywords=("התחלה", "מאיפה מתחילים", "סקירה", "כללי", "פונדקאות", "תהליך"),
    ),
    TopicPage(
        id="glossary",
        title_he="מונחון",
        category="start",
        keywords=("מושג", "מונח", "פירוש", "ראשי תיבות"),
    ),
    # --- Geography ---
    TopicPage(
        id="usa",
        title_he="ארצות הברית",
        category="geography",
        keywords=("ארהב", "ארצות הברית", "אמריקה", "usa", "us", "states", "מדינה", "ארה\"ב"),
    ),
    TopicPage(
        id="usa-california",
        title_he="ארצות הברית - קליפורניה",
        category="geography",
        parent="usa",
        keywords=("קליפורניה", "california", "la", "לוס אנגלס"),
    ),
    TopicPage(
        id="usa-states-legal",
        title_he="ארצות הברית - מדינות מומלצות משפטית",
        category="geography",
        parent="usa",
        keywords=("מדינה ידידותית", "surrogacy friendly", "טקסס", "נבדה", "אילינוי", "ניו יורק"),
    ),
    TopicPage(
        id="israel",
        title_he="ישראל",
        category="geography",
        keywords=("ישראל", "בארץ", "ועדת אישורים", "פונדקאות בישראל"),
    ),
    TopicPage(
        id="colombia",
        title_he="קולומביה",
        category="geography",
        keywords=("קולומביה", "colombia", "בוגוטה", "מדיין"),
    ),
    TopicPage(
        id="georgia",
        title_he="גאורגיה",
        category="geography",
        keywords=("גאורגיה", "georgia", "טביליסי"),
    ),
    TopicPage(
        id="cyprus",
        title_he="קפריסין",
        category="geography",
        keywords=("קפריסין", "cyprus", "ניקוסיה"),
    ),
    # --- Providers / agencies ---
    TopicPage(
        id="tamuz",
        title_he="תמוז (Tammuz)",
        category="providers",
        keywords=("תמוז", "tammuz", "tamuz"),
    ),
    TopicPage(
        id="orm",
        title_he="ORM",
        category="providers",
        keywords=("orm",),
    ),
    TopicPage(
        id="gaya",
        title_he="גאיה (Gaya)",
        category="providers",
        keywords=("גאיה", "gaya"),
    ),
    TopicPage(
        id="babybloom",
        title_he="בייביבלום (Babybloom)",
        category="providers",
        keywords=("בייביבלום", "babybloom"),
    ),
    TopicPage(
        id="surmom",
        title_he="סורמום (Surmom)",
        category="providers",
        keywords=("סורמום", "surmom"),
    ),
    TopicPage(
        id="ivy",
        title_he="Ivy Fertility Israel (Ivy)",
        category="providers",
        keywords=("ivy"),
    ),
    TopicPage(
        id="providers-other",
        title_he="סוכנויות נוספות",
        category="providers",
        keywords=("סוכנות", "agency", "ספק", "וקיל", "מתאם"),
    ),
    TopicPage(
        id="choosing-agency",
        title_he="איך בוחרים סוכנות",
        category="providers",
        keywords=("לבחור סוכנות", "השוואה", "המלצה על סוכנות", "חוזה עם סוכנות"),
    ),
    # --- Legal ---
    TopicPage(
        id="legal-parentage",
        title_he="הורות, צו הורות ותעודת לידה",
        category="legal",
        keywords=("צו הורות", "תעודת לידה", "הורות", "parentage", "birth certificate", "ניתוק זיקה"),
    ),
    TopicPage(
        id="legal-citizenship",
        title_he="אזרחות, דרכון ורישום",
        category="legal",
        keywords=("אזרחות", "דרכון", "קונסוליה", "משרד הפנים", "רישום", "passport", "אפוסטיל"),
    ),
    TopicPage(
        id="legal-lawyers",
        title_he="עורכי דין",
        category="legal",
        keywords=("עורך דין", "עו\"ד", "עוד", "lawyer", "attorney", "ייצוג משפטי"),
    ),
    TopicPage(
        id="legal-contracts",
        title_he="חוזים והסכמים",
        category="legal",
        keywords=("חוזה", "הסכם", "contract", "סעיף"),
    ),
    # --- Religion ---
    TopicPage(
        id="conversion",
        title_he="גיור",
        category="religion",
        keywords=("גיור", "להתגייר", "רבנות", "יהדות", "conversion", "בית דין", "רפורמי", "קונסרבטיבי", "ברית", "ברית מילה"),
    ),
    # --- Money ---
    TopicPage(
        id="money-costs",
        title_he="עלויות התהליך",
        category="money",
        keywords=("עלות", "מחיר", "כמה עולה", "תקציב", "cost", "budget", "דולר"),
    ),
    TopicPage(
        id="money-transfers",
        title_he="העברות כספים ואסקרו",
        category="money",
        keywords=("העברה", "אסקרו", "escrow", "חשבון נאמנות", "swift", "המרת מטבע"),
    ),
    TopicPage(
        id="money-tax-insurance",
        title_he="מסים, ביטוח והחזרים",
        category="money",
        keywords=("מס", "ביטוח", "החזר", "insurance", "tax", "ביטוח לאומי"),
    ),
    # --- Process stages ---
    TopicPage(
        id="egg-donor",
        title_he="תרומת ביציות ובחירת תורמת",
        category="process",
        keywords=("תורמת", "ביצית", "תרומת ביציות", "egg donor", "donor"),
    ),
    TopicPage(
        id="surrogate",
        title_he="בחירת פונדקאית והתנהלות מולה",
        category="process",
        keywords=("פונדקאית", "נושאת", "surrogate", "carrier", "התאמה"),
    ),
    TopicPage(
        id="ivf",
        title_he="הפריה והחזרת עוברים (IVF)",
        category="process",
        keywords=("הפריה", "עובר", "ivf", "embryo", "החזרה", "מעבדה"),
    ),
    TopicPage(
        id="pregnancy",
        title_he="הריון ומעקב",
        category="process",
        keywords=("הריון", "מעקב", "שליש", "אולטרסאונד", "pregnancy"),
    ),
    TopicPage(
        id="birth",
        title_he="לידה והשבועות הראשונים",
        category="process",
        keywords=("לידה", "יולדת", "בית חולים", "birth", "delivery", "תינוק"),
    ),
    TopicPage(
        id="bringing-baby-home",
        title_he="חזרה ארצה עם התינוק",
        category="process",
        keywords=("טיסה", "חזרה ארצה", "להביא הביתה", "דרכון לתינוק", "flight home"),
    ),
    # --- Medical ---
    TopicPage(
        id="clinics",
        title_he="מרפאות וצוות רפואי",
        category="medical",
        keywords=("מרפאה", "קליניקה", "רופא", "clinic", "doctor", "fertility"),
    ),
)


_BY_ID: dict[str, TopicPage] = {page.id: page for page in TAXONOMY}


def all_pages() -> tuple[TopicPage, ...]:
    return TAXONOMY


def get_page(topic_id: str) -> TopicPage | None:
    return _BY_ID.get(topic_id)


def page_ids() -> list[str]:
    return [page.id for page in TAXONOMY]


def category_title(category_id: str) -> str:
    return CATEGORIES.get(category_id, category_id)


def taxonomy_seed_block() -> str:
    """Compact seed listing of suggested wiki pages for LLM tagging/planning."""

    lines: list[str] = []
    for page in TAXONOMY:
        parent = f" (תת-נושא של {page.parent})" if page.parent else ""
        lines.append(f"- {page.id}: {page.title_he}{parent}")
    return "\n".join(lines)
