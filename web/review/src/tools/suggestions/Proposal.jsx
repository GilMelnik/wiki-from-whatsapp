import { Bidi, Consequences, Ltr, usePlanOverview, usePreview } from "@shell";
import { proposalOf, STRUCTURAL } from "./suggestions.js";

// What the suggestion would do, as it will be carried out: the owner's correction when there
// is one (the raised proposal folded under it). A text correction is the instruction to the
// page writer; a plan change is its fields and, while it can still be applied, the plan
// tool's dry run of what it would do downstream (what disappears, is redirected, is
// classified again), so the consequence is in view before deciding. New ids are the
// reviewer's guess, corrected in the edit dialog (e).

function Row({ label, children, ltr = false }) {
  if (children === undefined || children === null || children === "") return null;
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd>{ltr ? <Ltr>{children}</Ltr> : <Bidi className="block whitespace-pre-line leading-relaxed">{children}</Bidi>}</dd>
    </div>
  );
}

const FIELDS = {
  split: (p) => [
    ["Page that splits", p.page_id, true],
    ["New page id", p.new_page_id, true],
    ["Title", p.title_he],
    ["Description", p.description],
    ["Scope of the new page", p.scope],
    ["Scope of the old page after it", p.original_scope],
  ],
  merge: (p) => [
    ["Page that stays", p.survivor_id, true],
    ["Pages merged into it", p.merged_ids.join(", "), true],
  ],
  add_page: (p) => [
    ["Id", p.page_id, true],
    ["Inside the category", p.parent_id, true],
    ["Title", p.title_he],
    ["Description", p.description],
    ["Scope", p.scope],
  ],
  add_label: (p) => [
    ["Id", p.label_id, true],
    ["Title", p.title_he],
    ["Description", p.description],
    ["Kind", p.kind, true],
  ],
};

function Body({ kind, proposal }) {
  if (!STRUCTURAL.includes(kind)) {
    return (
      <Bidi as="p" className="card whitespace-pre-line leading-relaxed">
        {proposal.correction}
      </Bidi>
    );
  }
  return (
    <dl className="card space-y-2">
      {FIELDS[kind](proposal).map(([label, value, ltr]) => (
        <Row key={label} label={label} ltr={ltr}>
          {value}
        </Row>
      ))}
    </dl>
  );
}

function DryRun({ proposal }) {
  const plan = usePlanOverview();
  const { preview, error, pending } = usePreview(proposal);
  return (
    <section>
      <h3 className="section-title">What applying it does to the plan</h3>
      <Consequences preview={preview} error={error} pending={pending} overview={plan.data} />
    </section>
  );
}

const TITLES = {
  mistake: "Proposed fix (an instruction to the page writer)",
  contradiction: "Proposed way to show both sides",
};

export function Proposal({ suggestion }) {
  const edited = Boolean(suggestion.edited_proposal);
  const structural = STRUCTURAL.includes(suggestion.kind);
  const open = suggestion.status === "pending" || suggestion.status === "accepted";
  return (
    <>
      <section>
        <h3 className="section-title flex items-center gap-2">
          {TITLES[suggestion.kind] ?? "The proposed change"}
          {edited && <span className="badge badge-sky normal-case tracking-normal">corrected by you</span>}
        </h3>
        <Body kind={suggestion.kind} proposal={proposalOf(suggestion)} />
        {edited && (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer text-slate-500">As the review proposed it</summary>
            <div className="mt-1 opacity-75">
              <Body kind={suggestion.kind} proposal={suggestion.proposal} />
            </div>
          </details>
        )}
      </section>
      {structural && open && <DryRun proposal={proposalOf(suggestion)} />}
    </>
  );
}
