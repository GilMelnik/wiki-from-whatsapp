import { Bidi, Ltr } from "@shell";
import { proposalOf } from "./suggestions.js";

// What the suggestion would do: the proposal as it will be carried out (the owner's
// correction when there is one, the raised proposal folded under it). Structural proposals
// are plan operations; new ids are the reviewer's guess, corrected in the edit dialog (e).

function Row({ label, children, ltr = false }) {
  if (children === undefined || children === null || children === "") return null;
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd>{ltr ? <Ltr>{children}</Ltr> : <Bidi as="span" className="whitespace-pre-line leading-relaxed">{children}</Bidi>}</dd>
    </div>
  );
}

const FIELDS = {
  split: (p) => [
    ["הדף שמתפצל", p.page_id, true],
    ["מזהה הדף החדש", p.new_page_id, true],
    ["כותרת", p.title_he],
    ["תיאור", p.description],
    ["התחום של הדף החדש", p.scope],
    ["התחום של הדף המקורי אחרי הפיצול", p.original_scope],
  ],
  merge: (p) => [
    ["הדף שנשאר", p.survivor_id, true],
    ["הדפים שמתמזגים לתוכו", p.merged_ids.join(", "), true],
  ],
  add_page: (p) => [
    ["מזהה", p.page_id, true],
    ["בתוך הקטגוריה", p.parent_id, true],
    ["כותרת", p.title_he],
    ["תיאור", p.description],
    ["תחום", p.scope],
  ],
  add_label: (p) => [
    ["מזהה", p.label_id, true],
    ["כותרת", p.title_he],
    ["תיאור", p.description],
    ["סוג", p.kind, true],
  ],
  mistake: (p) => [
    ["הדף", p.page_id, true],
    ["המשפט", p.sentence],
    ["התיקון", p.correction],
  ],
  contradiction: (p) => [
    ["מה סותר", p.clash],
    ["איך להציג", p.correction],
    ["הדפים", p.pages.join(", "), true],
  ],
};

function Fields({ kind, proposal }) {
  return (
    <dl className="space-y-2 rounded border border-slate-200 bg-white p-3">
      {FIELDS[kind](proposal).map(([label, value, ltr]) => (
        <Row key={label} label={label} ltr={ltr}>
          {value}
        </Row>
      ))}
    </dl>
  );
}

export function Proposal({ suggestion }) {
  const edited = Boolean(suggestion.edited_proposal);
  return (
    <section>
      <h3 className="section-title">
        ההצעה {edited && <span className="badge badge-sky ms-1">תוקנה על ידיכם</span>}
      </h3>
      <Fields kind={suggestion.kind} proposal={proposalOf(suggestion)} />
      {edited && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-500">ההצעה כפי שהסקירה העלתה אותה</summary>
          <div className="mt-1 opacity-75">
            <Fields kind={suggestion.kind} proposal={suggestion.proposal} />
          </div>
        </details>
      )}
    </section>
  );
}
