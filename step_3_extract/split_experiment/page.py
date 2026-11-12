"""The owner's page of the split experiment: one local HTML file, no external assets.

On top, the table of every variant (cost, claims, matching against B) and how claims are
matched. Then one section per gold window: its messages in chat order (date and time, sender,
the owner's conversation as a coloured mark), and next to each message one column per variant
with the claims that rest on it, placed at the claim's first supporting message. A claim of B
that a variant did not match shows as an empty "missing" box in that variant's column, on the
same row; a claim no claim of B matches says "not in B". Where a variant's unit (call) starts, its
column shows the unit id, so the cuts can be compared too. English interface, Hebrew content
right to left, light and dark (``prefers-color-scheme``).

The page holds the chat's text and the claims: it is PRIVATE (written under the data dir only).
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from step_3_extract.split_experiment.compare import REFERENCE, ROWS, VariantRun, shown
from step_3_extract.split_experiment.units import EXPERIMENT_CONFIG, MessageSet
from utils.threads_io import format_reactions_for_llm

NAMES: dict[str, str] = EXPERIMENT_CONFIG["variants"]
_HUE_STEP = 137  # degrees between two conversations' colours: neighbours never look alike
_QUOTE_CHARS = 90


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _moment(record: Mapping[str, Any]) -> str:
    return str(record.get("datetime") or "").replace("T", " ")[:16]


class PageWriter:
    """Builds the page from the runs (columns in the order B, then the others)."""

    def __init__(self, runs: Mapping[str, VariantRun], messages: MessageSet,
                 columns: Mapping[str, Mapping[str, Any]],
                 matches: Mapping[str, Mapping[str, str]], metadata: Mapping[str, Any]) -> None:
        self.runs = runs
        self.messages = messages
        self.columns = columns
        self.matches = matches
        self.metadata = metadata
        self.order = sorted(runs, key=lambda v: (v != REFERENCE, v))
        self.label: dict[str, str] = {}  # claim id -> "A12"
        for variant in self.order:
            for number, claim in enumerate(runs[variant].claims, start=1):
                self.label[claim["claim_id"]] = f"{variant}{number}"
        self.unit_of = {v: {wa_id: unit for unit in run.units for wa_id in unit.message_ids}
                        for v, run in runs.items()}

    def anchor(self, claim: Mapping[str, Any]) -> str:
        """The claim's first supporting message in chat order (where the page shows it)."""
        return min(claim["supporting_message_ids"], key=self.messages.position.__getitem__)

    def page(self) -> str:
        meta = self.metadata
        dry = ("<p class='banner'>Dry run: a stand-in model wrote placeholder claims; no number "
               "on this page is real.</p>" if meta.get("dry_run") else "")
        nav = " · ".join(f"<a href='#w{w + 1}'>window {w + 1}</a>" for w in self.messages.windows)
        sections = "".join(self.window(w) for w in self.messages.windows)
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Split experiment</title><style>{_STYLE}</style></head><body>"
            f"<h1>Split experiment: the same messages cut five ways</h1>{dry}"
            f"<p>{self.messages.size()} messages in {len(self.messages.windows)} gold windows. "
            f"Model {_esc(meta.get('model'))}, {'batch' if meta.get('batch') else 'sync'} API; "
            f"generated {_esc(meta.get('generated_at'))}, commit "
            f"<code>{_esc(str(meta.get('git_commit'))[:10])}</code>.</p>"
            f"{self.legend()}{self.summary()}<nav>{nav}</nav>{sections}</body></html>")

    def legend(self) -> str:
        items = "".join(f"<li><b>{v}</b> {_esc(NAMES[v])}</li>" for v in self.order)
        return (f"<ul class='legend'>{items}</ul><p class='note'>Two claims match when the "
                f"messages they rest on overlap: shared messages divided by the smaller claim's "
                f"messages at least {EXPERIMENT_CONFIG['match_min_overlap']}, one to one, best "
                "overlap first, within a window. Found / missed / extra are counted against B. "
                "A match says the claims rest on the same messages, not that they say the same: "
                "read them side by side below.</p>")

    def summary(self) -> str:
        head = "".join(f"<th>{v}<br><small>{_esc(NAMES[v])}</small></th>" for v in self.order)
        rows = "".join(
            f"<tr><td>{_esc(label)}</td>"
            + "".join(f"<td>{_esc(shown(self.columns[v].get(key)))}</td>" for v in self.order)
            + "</tr>"
            for key, label in ROWS if any(key in self.columns[v] for v in self.order))
        return f"<h2>Summary</h2><table class='summary'><tr><th></th>{head}</tr>{rows}</table>"

    def window(self, window: int) -> str:
        ids = self.messages.ids[window]
        start, end = (moment.replace("T", " ") for moment in self.messages.spans[window])
        claims = {v: [c for c in self.runs[v].claims if c["window"] == window] for v in self.order}
        at: dict[str, dict[str, list[str]]] = {v: {} for v in self.order}
        for variant, found in claims.items():
            for claim in found:
                at[variant].setdefault(self.anchor(claim), []).append(self.claim(variant, claim))
        if REFERENCE in self.runs:
            for claim in claims[REFERENCE]:
                for variant in self.order[1:]:
                    if claim["claim_id"] not in self.matches[variant].values():
                        at[variant].setdefault(self.anchor(claim), []).append(
                            f"<div class='missing'>no claim for {self.label[claim['claim_id']]}"
                            "</div>")
        counts = " · ".join(f"{v}: {len(claims[v])} claims" for v in self.order)
        hues = {label: (n * _HUE_STEP) % 360 for n, label in
                enumerate(dict.fromkeys(self.messages.labels[i] for i in ids))}
        head = "".join(f"<th>{v} <small>{_esc(NAMES[v])}</small></th>" for v in self.order)
        rows: list[str] = []
        previous: dict[str, str] = {}
        for number, wa_id in enumerate(ids, start=1):
            if number > 1:
                gap = self.messages.position[wa_id] - self.messages.position[ids[number - 2]] - 1
                if gap:
                    rows.append(f"<tr class='gap'><td colspan='{len(self.order) + 2}'>{gap} chat "
                                "messages not in the set</td></tr>")
            cells = "".join(self.cell(v, wa_id, previous, at[v].get(wa_id, []))
                            for v in self.order)
            rows.append(f"<tr id='m{window + 1}-{number}'><td class='num'>#{number}</td>"
                        f"{self.message(wa_id, hues)}{cells}</tr>")
        return (f"<section id='w{window + 1}'><h2>Window {window + 1}: {_esc(start)} → "
                f"{_esc(end)}</h2><p>{len(ids)} messages, {len(hues)} conversations as the "
                f"owner tagged them. {counts}.</p><div class='scroll'><table class='grid'><thead><tr>"
                f"<th>#</th><th>message</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody>"
                "</table></div></section>")

    def message(self, wa_id: str, hues: Mapping[str, int]) -> str:
        record = self.messages.records[wa_id]
        label = self.messages.labels[wa_id]
        normal = self.messages.thread("", [wa_id])["messages"][0]
        quote = normal.get("quote")
        replied = ""
        if quote:
            text = " ".join(quote["text"].split())
            short = text if len(text) <= _QUOTE_CHARS else text[:_QUOTE_CHARS] + "…"
            replied = f"<div class='quote' dir='auto'>↩ {_esc(short)}</div>"
        reactions = format_reactions_for_llm(normal.get("reactions"))
        conversation = list(hues).index(label) + 1
        return (f"<td class='msg' style='--hue:{hues[label]}'><div class='meta'>"
                f"{_esc(_moment(record))} · {_esc(record.get('sender'))} · "
                f"<span class='conv'>conversation {conversation}</span></div>{replied}"
                f"<div class='text' dir='auto'>{_esc(record.get('content'))}</div>"
                f"<div class='meta' dir='auto'>{_esc(reactions)}</div></td>")

    def cell(self, variant: str, wa_id: str, previous: dict[str, str], items: Sequence[str]
             ) -> str:
        unit = self.unit_of[variant].get(wa_id)
        tag = ""
        classes = "cell"
        if unit is not None:
            part = next(n for n, c in enumerate(unit.conversations, start=1) if wa_id in c)
            current = f"{unit.unit_id}/{part}"
            if previous.get(variant) != current:
                classes += " start"
                extra = (f", {len(unit.background)} background" if unit.background else "")
                if len(unit.conversations) > 1:
                    extra += f", conversation {part} of {len(unit.conversations)}"
                tag = f"<div class='unit'>{unit.unit_id}{extra}</div>"
            previous[variant] = current
        return f"<td class='{classes}'>{tag}{''.join(items)}</td>"

    def claim(self, variant: str, claim: Mapping[str, Any]) -> str:
        rows = {wa_id: n for n, wa_id in
                enumerate(self.messages.ids[claim["window"]], start=1)}
        rests = ", ".join(f"#{rows[i]}" for i in claim["supporting_message_ids"] if i in rows)
        notes = [claim["stance"], _people(claim["supporter_count"], "supporter"),
                 f"rests on {rests}"]
        if claim["opposer_count"]:
            notes.append(_people(claim["opposer_count"], "opposer"))
        matched = self.matches.get(variant, {}).get(claim["claim_id"])
        classes = f"claim {claim['stance']}"
        if variant != REFERENCE and REFERENCE in self.runs:
            notes.append(f"≈ {self.label[matched]}" if matched else "not in B")
            classes += "" if matched else " extra"
        if claim["counters"]:
            notes.append("disputes " + ", ".join(self.label.get(c, c) for c in claim["counters"]))
        if claim["cites_background"]:
            notes.append("cited a background message")
        if claim["conversations"] > 1:
            notes.append(f"rests on {claim['conversations']} conversations")
        return (f"<div class='{classes}'><div class='head'><b>{self.label[claim['claim_id']]}"
                f"</b> {_esc(' · '.join(notes))}</div>"
                f"<div class='text' dir='auto'>{_esc(claim['claim_text'])}</div></div>")


def _people(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def write_page(path: Path, runs: Mapping[str, VariantRun], messages: MessageSet,
               columns: Mapping[str, Mapping[str, Any]],
               matches: Mapping[str, Mapping[str, str]], metadata: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PageWriter(runs, messages, columns, matches, metadata).page(),
                    encoding="utf-8")


_STYLE = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1d1d1f; --muted: #6b6b73;
  --line: #d6d6dc; --panel: #f4f4f7; --claim: #ffffff; --missing: #b3261e; --banner: #fff4d6;
  --pos: #1e7f4f; --neg: #b3261e; --neu: #6b6b73; --fact: #2457c5; --head: #ececf1; --conv-l: 38%; }
@media (prefers-color-scheme: dark) { :root { --bg: #16161a; --fg: #e9e9ee; --muted: #a0a0aa;
  --line: #3a3a42; --panel: #202026; --claim: #26262d; --missing: #f2877e; --banner: #3d3214;
  --pos: #5fd39a; --neg: #f2877e; --neu: #a0a0aa; --fact: #8fb0ff; --head: #2a2a31; --conv-l: 72%; } }
body { background: var(--bg); color: var(--fg); font: 14px/1.5 system-ui, sans-serif;
  margin: 0; padding: 16px; }
h1 { font-size: 22px; margin: 0 0 8px; } h2 { font-size: 18px; margin: 28px 0 6px; }
a { color: var(--fact); } code { font-size: 12px; }
.banner { background: var(--banner); padding: 8px 12px; border-radius: 6px; }
.note, small { color: var(--muted); } .legend { padding-left: 20px; }
nav { margin: 16px 0; } section { border-top: 2px solid var(--line); margin-top: 24px; }
table { border-collapse: collapse; }
.summary td, .summary th { border: 1px solid var(--line); padding: 3px 10px; }
.summary td + td { text-align: right; font-variant-numeric: tabular-nums; }
.summary th { background: var(--head); }
.scroll { overflow: auto; max-height: 85vh; border: 1px solid var(--line); }
.grid { min-width: 1500px; }
.grid th { position: sticky; top: 0; background: var(--head); z-index: 1; padding: 4px 8px;
  text-align: left; border-bottom: 1px solid var(--line); }
.grid td { border-bottom: 1px solid var(--line); vertical-align: top; padding: 4px 6px; }
.num { color: var(--muted); font-size: 12px; white-space: nowrap; }
.msg { width: 340px; border-left: 5px solid hsl(var(--hue) 65% 50%); background: var(--panel); }
.msg .text { white-space: pre-wrap; overflow-wrap: anywhere; }
.meta, .quote { color: var(--muted); font-size: 12px; }
.conv { color: hsl(var(--hue) 65% var(--conv-l)); font-weight: 600; }
.cell { width: 230px; } .cell.start { border-top: 2px solid var(--muted); }
.unit { font-size: 11px; color: var(--muted); }
.claim { background: var(--claim); border: 1px solid var(--line); border-left: 4px solid var(--neu);
  border-radius: 4px; padding: 3px 6px; margin: 3px 0; }
.claim.positive { border-left-color: var(--pos); } .claim.negative { border-left-color: var(--neg); }
.claim.factual { border-left-color: var(--fact); } .claim.extra { border-style: dashed; }
.claim .head { font-size: 11px; color: var(--muted); } .claim .head b { color: var(--fg); }
.missing { border: 1px dashed var(--missing); color: var(--missing); border-radius: 4px;
  padding: 3px 6px; margin: 3px 0; font-size: 12px; min-height: 28px; }
.gap td { text-align: center; color: var(--muted); font-size: 12px; background: var(--panel); }
"""
