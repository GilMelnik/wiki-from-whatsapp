"""What step 3 sends to the model and the JSON it asks back (schema-enforced).

The model returns only what needs judgement: claim text, entities, stance and the numbers of
the supporting / opposing messages as shown in the rendered thread. Ids, dates, counts and
reaction tallies are computed in code (``step_3_extract.claims``).

One system prompt serves both modes, so its cached prefix is shared:
- fresh: the whole thread -> ``FRESH_SCHEMA`` ``{claims}``;
- update (the thread gained messages): the thread with the new messages marked plus its
  existing claims as ``C1``, ``C2``, ... -> ``UPDATE_SCHEMA`` ``{claims, existing}``.
"""

from __future__ import annotations

from typing import Any

from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from utils.threads_io import RENDER_CONFIG

STANCES = ("positive", "negative", "neutral", "factual")

_NUMBERS: dict[str, Any] = {"type": "array", "items": {"type": "integer"}}
_CLAIM_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "entities": {"type": "array", "items": {"type": "string"}},
        "stance": {"type": "string", "enum": list(STANCES)},
        "supporting": _NUMBERS,
        "opposing": _NUMBERS,
    },
    "required": ["text", "entities", "stance", "supporting", "opposing"],
}
FRESH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"claims": {"type": "array", "items": _CLAIM_ITEM}},
    "required": ["claims"],
}
UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {"type": "array", "items": _CLAIM_ITEM},
        "existing": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "integer"},
                    "supporting": _NUMBERS,
                    "opposing": _NUMBERS,
                },
                "required": ["claim", "supporting", "opposing"],
            },
        },
    },
    "required": ["claims", "existing"],
}

_ALIAS_EXAMPLE = RENDER_CONFIG["alias"].format(n=1)
_PREFIX = RENDER_CONFIG["existing_claim_prefix"]
_REPLY = RENDER_CONFIG["reply_mark"]

EXTRACT_SYSTEM = f"""\
You extract knowledge from one conversation of an Israeli WhatsApp group of gay men who are \
having children through surrogacy. The claims you write become the source of a Hebrew wiki \
for the community.

How the conversation is shown
- Every message is one entry: "[n] YYYY-MM-DD <speaker>: <text>". n is the message number, \
the speaker is an alias such as "{_ALIAS_EXAMPLE}"; the same alias is the same person \
throughout this conversation.
- "{_REPLY}[k]" after the speaker means the message replies to message k. \
"{_REPLY}«<speaker>: <text>»" quotes an earlier message that is not shown.
- "[{RENDER_CONFIG['reactions_label']}: 👍×3 ...]" lists the emoji reactions the message got. \
Never cite reactions: they are counted separately.
- "{RENDER_CONFIG['new_marker']}" marks messages added since the conversation was last \
processed (see "Updating").

What to extract
- Claims: specific, useful knowledge another member could act on: recommendations, \
warnings, facts, costs, timelines, procedures, requirements, personal experience, legal, \
medical and financial information.
- Each claim is one self-contained sentence in natural, neutral Hebrew that is clear without \
the conversation: name its subject (country, agency, clinic, doctor, lawyer, procedure, \
document) and keep the concrete details (amounts and currencies, durations, dates, places).
- One idea per claim. Do not merge unrelated facts and do not split one fact into \
near-duplicates. Do not write the same claim twice.
- Skip small talk, greetings, congratulations, arrangements between specific members, \
giveaways and sales of equipment, and off-topic arguments. When nothing is worth keeping, \
return an empty list.
- Privacy: never write names, nicknames, phone numbers, e-mail addresses or other identifying \
details of group members, and never mention speaker aliases. Say "חבר בקבוצה" / \
"חברים" instead. Keep the names of businesses and professionals (agencies, clinics, \
doctors, lawyers, travel agents, insurers and other providers) and of countries and places.
- {FORBIDDEN_TERM_INSTRUCTION}

Fields of a claim
- text: the claim, in Hebrew.
- entities: the providers, organisations, countries, cities and places the claim is about, \
spelled as in the conversation. Empty when there are none.
- stance: "positive" recommends or praises; "negative" warns or criticises; "neutral" is an \
opinion or experience without a clear valence; "factual" is a fact, price, rule or procedure.
- supporting: numbers of the messages that state, confirm or agree with the claim; always at \
least the message(s) the claim comes from.
- opposing: numbers of the messages that dispute or contradict the claim or report a contrary \
experience; usually empty.
- A message number is never in both lists of one claim. When members disagree, write the \
claim once and put the dissenting messages in opposing; write the dissent as a claim of its \
own only when it adds information of its own.

Updating (only when the request lists existing claims)
- The conversation was processed before. Its claims are listed as {_PREFIX}1, {_PREFIX}2, ... \
They are final: never repeat, rephrase or correct them.
- claims: only knowledge that comes from the messages marked {RENDER_CONFIG['new_marker']} \
and is not already covered by an existing claim; each new claim's supporting list includes at \
least one marked message.
- existing: one entry for each existing claim that marked messages support or oppose: \
claim is its number (1 for {_PREFIX}1); supporting and opposing list only marked messages.
"""


def build_fresh_prompt(rendered: str) -> str:
    return f"Conversation:\n{rendered}"


def build_update_prompt(rendered: str, existing_texts: list[str]) -> str:
    listed = "\n".join(
        f"{_PREFIX}{number}: {text}" for number, text in enumerate(existing_texts, start=1)
    )
    return f"Existing claims:\n{listed}\n\nConversation:\n{rendered}"
