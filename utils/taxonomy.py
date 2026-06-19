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
    "parenting": "הורות ותינוקות",
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
        keywords=("ישראל", "בארץ", "ועדת אישורים", "פונדקאות בישראל", "הטסת עוברים"),
    ),
    TopicPage(
        id="colombia",
        title_he="קולומביה",
        category="geography",
        keywords=("קולומביה", "colombia", "בוגוטה", "מדיין"),
    ),
    TopicPage(
        id="argentina",
        title_he="ארגנטינה",
        category="geography",
        keywords=("ארגנטינה", "argentina", "בואנוס איירס", "buenos aires"),
    ),
    TopicPage(
        id="armenia",
        title_he="ארמניה",
        category="geography",
        keywords=("ארמניה", "armenia"),
    ),
    TopicPage(
        id="mexico",
        title_he="מקסיקו",
        category="geography",
        keywords=("מקסיקו", "mexico", "מקסיקו סיטי"),
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
    TopicPage(
        id="canada",
        title_he="קנדה",
        category="geography",
        keywords=("קנדה", "Canada", "AHRA", "altruistic", "פונדקאות בקנדה", "parentage קנדה"),
    ),
    TopicPage(
        id="surrogacy-warnings",
        title_he="אזהרות משרד המשפטים — יעדים בעייתיים",
        category="geography",
        keywords=(
            "אזהרה",
            "אזהרת מסע",
            "משרד המשפטים",
            "אלבניה",
            "albania",
            "קניה",
            "kenya",
            "צפון קפריסין",
            "north cyprus",
        ),
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
        keywords=("סורמום", "surmom", "סורמאם"),
    ),
    TopicPage(
        id="ivy",
        title_he="Ivy Fertility Israel (Ivy)",
        category="providers",
        keywords=("ivy", "ארז ויניב"),
    ),
    TopicPage(
        id="amit-peles",
        title_he="עמית פלס – סוכנת נסיעות",
        category="providers",
        keywords=("עמית פלס", "סוכנת נסיעות", "טיסות", "כרטיסי טיסה"),
    ),
    TopicPage(
        id="choosing-agency",
        title_he="איך בוחרים סוכנות",
        category="providers",
        keywords=("לבחור סוכנות", "השוואה", "המלצה על סוכנות", "חוזה עם סוכנות", "תהליך עצמאי", "סוכנות"),
    ),
    TopicPage(
        id="providers-other",
        title_he="סוכנויות וספקים נוספים",
        category="providers",
        keywords=(
            "מיכל",
            "מיכל קרן דוד",
            "US Surrogacy Consultant",
            "The Fertility Agency",
            "Blossom",
            "Surrogate Steps",
            "Dream Surrogacy",
            "SurroConnections",
            "IARC",
            "Arkcryo",
            "Life Parcel",
            "MHB",
            "Men Having Babies",
            "GPAP",
            "אבישי זאבי",
        ),
    ),
    # --- Legal ---
    TopicPage(
        id="legal-parentage",
        title_he="הורות, צו הורות ותעודת לידה",
        category="legal",
        keywords=("צו הורות", "תעודת לידה", "הורות", "parentage", "birth certificate", "ניתוק זיקה"),
    ),
    TopicPage(
        id="europe-post-birth-bureaucracy",
        title_he="רישום ואזרוח באירופה",
        category="legal",
        parent="legal-parentage",
        keywords=("רומניה", "romania", "הונגריה", "hungary", "שני אבות", "תעודת לידה", "ביורוקרטיה", "פורטוגל", "אזרחות"),
    ),
    TopicPage(
        id="legal-citizenship",
        title_he="אזרחות, דרכון ורישום",
        category="legal",
        keywords=("אזרחות", "דרכון", "קונסוליה", "משרד הפנים", "רישום", "passport", "אפוסטיל", "ssn"),
    ),
    TopicPage(
        id="legal-lawyers",
        title_he="עורכי דין",
        category="legal",
        keywords=("עורך דין", "עו\"ד", "עוד", "lawyer", "attorney", "ייצוג משפטי", "איילת טרסר", "ויקטוריה גלפנד", "ויקטוריה", "הראל", "הראל ברק"),
    ),
    TopicPage(
        id="legal-contracts",
        title_he="חוזים והסכמים",
        category="legal",
        keywords=("חוזה", "הסכם", "contract", "סעיף"),
    ),
    TopicPage(
        id="legal-marriage",
        title_he="נישואין",
        category="legal",
        keywords=("נישואין", "נישואי יוטה", "יוטה", "utah", "zoom wedding", "Marry From Home"),
    ),
    # --- Religion ---
    TopicPage(
        id="conversion",
        title_he="גיור",
        category="religion",
        keywords=("גיור", "להתגייר", "רבנות", "יהדות", "conversion", "בית דין", "רפורמי", "קונסרבטיבי", "ברית", "ברית מילה", "הלכה"),
    ),
    # --- Money ---
    TopicPage(
        id="money-transfers",
        title_he="העברה והמרת כספים",
        category="money",
        keywords=("העברה", "swift", "המרת מטבע", "דולר", "דולרים", "בנק", "gmt"),
    ),
    TopicPage(
        id="escrow",
        title_he="חשבון נאמנות Escrow",
        category="money",
        keywords=("אסקרו", "escrow", "חשבון נאמנות", "seedtrust", "seed trust"),
    ),
    TopicPage(
        id="tax",
        title_he="מסים והחזרים",
        category="money",
        keywords=("מס", "מסים", "tax", "ניכוי מס", "מס הכנסה", "החזר מס"),
    ),
    TopicPage(
        id="insurance-surrogate",
        title_he="ביטוח רפואי לפונדקאית",
        category="money",
        keywords=(
            "ביטוח פונדקאית",
            "surrogacy-friendly",
            "deductible",
            "OBGYN",
            "in-network",
            "פוליסת בריאות של הפונדקאית",
            "ACA",
            "אובמה קר"
        ),
    ),
    TopicPage(
        id="insurance-newborn",
        title_he="ביטוח לתינוק שנולד",
        category="money",
        keywords=(
            "Art Risk",
            "Great Morning",
            "DavidShield",
            "ביטוח יילודים",
            "פגייה",
            "NICU",
            "Wellcome",
            "ביטוח תינוק",
        ),
    ),
    TopicPage(
        id="insurance-hospital-bills",
        title_he="תשלום חשבונות בית חולים ורופאים אחרי הלידה",
        category="money",
        keywords=(
            "billing",
            "hospital bill",
            "self-pay",
            "pediatrician",
            "חשבונית",
            "חיוב רפואי",
            "Billing Management",
            "בית חולים",
        ),
    ),
    TopicPage(
        id="insurance-bituach-leumi",
        title_he='ביטוח לאומי',
        category="money",
        keywords=(
            "ביטוח לאומי",
            "מענק לידה",
            "דמי לידה",
            "החזר אשפוז",
            "קצבת ילדים",
            "מיצוי זכויות",
            "סיוון",
            "סיוון מביטוח לאומי",
        ),
    ),
    TopicPage(
        id="insurance-israeli-private",
        title_he="ביטוח פרטי בישראל",
        category="money",
        keywords=(
            "הראל",
            "הפניקס",
            "כלל",
            "מנורה",
            "מגדל",
            "קופת חולים",
            "החזר תרומת ביצית",
            "תביעה ייצוגית",
            'שב"ן',
        ),
    ),
    TopicPage(
        id="tax-irs-children",
        title_he='דיווח מס ל-IRS לילדים שנולדו בארה"ב',
        category="money",
        keywords=(
            "IRS",
            "FBAR",
            "Digitax",
            "מס אמריקאי",
            "ילד אמריקאי",
            "child savings plan",
            "Men Having Babies tax",
            "דיווח",
            "חיסכון לכל ילד",
        ),
    ),
    TopicPage(
        id="money-costs",
        title_he="עלויות התהליך",
        category="money",
        keywords=("עלות", "עלויות", "מחיר", "כמה עולה", "תקציב", "חיסכון", "cost", "תשלום לפונדקאית"),
    ),
    # --- Process stages ---
    TopicPage(
        id="egg-donor",
        title_he="תרומת ביציות ובחירת תורמת",
        category="process",
        keywords=("תורמת", "ביצית", "תרומת ביציות", "egg donor", "donor", "frozen eggs", "donor selection"),
    ),
    TopicPage(
        id="egg-donor-genetics",
        title_he="בדיקות גנטיות לתורמת ולהורים",
        category="process",
        parent="egg-donor",
        keywords=(
            "בדיקות גנטיות",
            "משטח גנטי",
            "Sema4",
            "Invitae",
            "PGS",
            "Igenomix",
            "פאנל אתני",
            "פאנל",
            "פאנל גנטי",
        ),
    ),
    TopicPage(
        id="surrogate",
        title_he="בחירת פונדקאית והתנהלות מולה",
        category="process",
        keywords=("פונדקאית", "נושאת", "surrogate", "carrier", "התאמה", "relationship", "מאצ'", "match"),
    ),
    TopicPage(
        id="surrogate-gifts",
        title_he="מתנות לפונדקאית",
        category="process",
        keywords=(
            "מתנות",
            "מתנה",
            "Sugarwish",
            "Amazon",
            "gift card",
            "יום הולדת פונדקאית",
            "אננס",
            "החזרת עוברים מתנה",
            "קריסמס",
        ),
    ),
    TopicPage(
        id="ivf",
        title_he="הפריה והחזרת עוברים (IVF)",
        category="process",
        keywords=("הפריה", "עובר", "ivf", "embryo", "החזרה", "מעבדה", "עוברים", "PTG", "PGT-A"),
    ),
    TopicPage(
        id="pregnancy",
        title_he="הריון ומעקב",
        category="process",
        keywords=("הריון", "מעקב", "שליש", "אולטרסאונד", "pregnancy", "בדיקות"),
    ),
    TopicPage(
        id="birth",
        title_he="לידה והשבועות הראשונים",
        category="process",
        keywords=("לידה", "יולדת", "בית חולים", "birth", "delivery", "תינוק", "צהבת", "חיסון"),
    ),
    TopicPage(
        id="bringing-baby-home",
        title_he="חזרה ארצה עם התינוק",
        category="legal",
        keywords=("חזרה ארצה", "להביא הביתה", "דרכון לתינוק", "מספר תעודת זהות", "ת.ז. זמני", "טיפת חלב"),
    ),
    TopicPage(
        id="rights",
        title_he="מיצוי זכויות",
        category="legal",
        keywords=("חופשת לידה", "מענק לידה", "דמי לידה", "ביטוח לאומי"),
    ),
    TopicPage(
        id="travel-with-baby",
        title_he="טיסות, לוגיסטיקת נסיעות עם תינוק",
        category="process",
        keywords=("טיסה", "טיסות", "flight", "airbnb", "עריסה", "el al", "travel", "אל על", "אלעל", "delta", "דלתא", "ארקיע", "united", "יונייטד", "השכרת רכב"),
        parent="bringing-baby-home",
    ),
    # --- Parenting & baby care ---
    TopicPage(
        id="books-and-media",
        title_he="ספרים ותכנים לילדים",
        category="parenting",
        keywords=("ספר", "book", "ילדים", "media", "הסבר לילד", "סיפור", "שאלות"),
    ),
    TopicPage(
        id="baby-formula",
        title_he='תמ"ל ואכילת תינוק',
        category="parenting",
        keywords=("תמ\"ל", "פורמולה", "formula", "האכלה", "בקבוק", "חלב", "חלב שאוב", "בהקפאה"),
        parent="birth",
    ),
    TopicPage(
        id="baby-gear",
        title_he="ציוד לתינוקות",
        category="parenting",
        keywords=("ציוד", "עגלה", "סלקל", "מנשא", "baby gear", "registry", "עגלות", "כסא לאוטו"),
        parent="birth",
    ),
    TopicPage(
        id="baby-development",
        title_he="התפתחות התינוק וקורסי הכנה",
        category="parenting",
        keywords=("התפתחות", "עיסוי תינוקות", "קורס הכנה ללידה", "כללית", "דיגיטל", "דיגיטף"),
        parent="birth",
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
