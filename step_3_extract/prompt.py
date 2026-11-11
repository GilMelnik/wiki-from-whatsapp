"""What step 3 sends to the model and the JSON it asks back (schema-enforced).

The model returns only what needs judgement: claim text, entities, stance and the numbers of
the supporting / opposing messages as shown in the rendered thread. Ids, dates, counts and
reaction tallies are computed in code (``step_3_extract.claims``).

One system prompt serves both modes, so its cached prefix is shared:
- fresh: the whole thread -> ``FRESH_SCHEMA`` ``{claims}``;
- update (the thread gained messages): the thread with the new messages marked plus its
  existing claims as ``C1``, ``C2``, ... -> ``UPDATE_SCHEMA`` ``{claims, existing}``.

Disagreements are recorded here, where the model sees who answered whom: a dissent of
substance becomes a claim of its own whose ``counters`` lists the claims it disputes by their
number in the answer (1 = the first claim of ``claims``); in update mode
``counters_existing`` lists existing claims it disputes (1 = ``C1``). Code turns them into
claim ids and makes the link mutual (``ThreadClaims.absorb``).
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
        "counters": _NUMBERS,
    },
    "required": ["text", "entities", "stance", "supporting", "opposing", "counters"],
}
# In update mode a new claim may also dispute an existing one.
_UPDATE_CLAIM_ITEM: dict[str, Any] = {
    **_CLAIM_ITEM,
    "properties": {**_CLAIM_ITEM["properties"], "counters_existing": _NUMBERS},
    "required": [*_CLAIM_ITEM["required"], "counters_existing"],
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
        "claims": {"type": "array", "items": _UPDATE_CLAIM_ITEM},
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
- counters: the claims of your answer that this claim disputes, by their number in your list \
(1 for the first claim you write); usually empty (see "When members disagree").
- counters_existing (only when the request lists existing claims): the existing claims this \
new claim disputes, by number (1 for {_PREFIX}1).
- A message number is never in both lists of one claim.

When members disagree
- Keep both sides. The claim that is disputed lists the dissenting messages in opposing.
- When the dissent says anything of substance (a different experience, a correction, a reason, \
a newer or different fact, price or timeline), also write the dissenting position as a claim of \
its own: self-contained like every claim, with its own stance, the dissenting messages in its \
supporting, and the disputed claim in its counters (an existing claim in counters_existing). \
Lean towards writing it: the wiki should say what the other side said, not only that someone \
disagreed. Write the position itself, never who held it.
- Only a dissent that says nothing beyond "no", "not true" or "I disagree" stays a number in the \
disputed claim's opposing, with no claim of its own.
- counters links claims that disagree, nothing else: not a claim that adds a detail to, narrows \
or updates one it agrees with, and not two claims that are merely about the same subject. One \
side listing the other is enough.

Updating (only when the request lists existing claims)
- The conversation was processed before. Its claims are listed as {_PREFIX}1, {_PREFIX}2, ... \
They are final: never repeat, rephrase or correct them.
- claims: only knowledge that comes from the messages marked {RENDER_CONFIG['new_marker']} \
and is not already covered by an existing claim; each new claim's supporting list includes at \
least one marked message.
- existing: one entry for each existing claim that marked messages support or oppose: \
claim is its number (1 for {_PREFIX}1); supporting and opposing list only marked messages.
- A marked message that disputes an existing claim goes in that claim's opposing; when it says \
anything of substance, it also becomes a new claim whose counters_existing lists that claim.
"""


def build_fresh_prompt(rendered: str) -> str:
    return f"Conversation:\n{rendered}"


def build_update_prompt(rendered: str, existing_texts: list[str]) -> str:
    listed = "\n".join(
        f"{_PREFIX}{number}: {text}" for number, text in enumerate(existing_texts, start=1)
    )
    return f"Existing claims:\n{listed}\n\nConversation:\n{rendered}"
