"""The forbidden term: the surrogate is never the child's "mother".

Steps 3, 6 and 8 put ``FORBIDDEN_TERM_INSTRUCTION`` into their prompts; step 3 also corrects
the term in code (``step_3_extract.scrub.correct_surrogate_terminology``).
"""

from __future__ import annotations

# Shared prompt directive so every LLM stage avoids producing the term at all.
FORBIDDEN_TERM_INSTRUCTION = (
    'אסור בתכלית האיסור להשתמש בצירוף "אמא פונדקאית" / "אם פונדקאית" / '
    '"האמא הפונדקאית" / "אמהות פונדקאיות" או כל הטיה אחרת המתארת את הפונדקאית '
    'כ"אם" או כ"אמא" — זהו מונח שגוי ופוגעני. השתמש אך ורק ב"פונדקאית" '
    '(או "נושאת").'
)
