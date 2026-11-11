import { Bidi, formatDateTime, Ltr, Panel, PanelState } from "@shell";
import { Changes } from "./Changes.jsx";
import { Groups, Pages, Quote, Section, Sides, StructuralEvidence } from "./Evidence.jsx";
import { Proposal } from "./Proposal.jsx";
import { KIND_BADGES, outcomeFor, proposalOf, STATUS_BADGES } from "./suggestions.js";

// The main column for one suggestion, in the order the owner decides: what changed on its
// pages since the review, the last failed apply, what an apply changed; then the reviewer's
// reason, the context it is about (the sentence in its paragraph, the two sides, the pages),
// the proposal next to it (with the dry run of a plan change), and the claim groups behind it.

export function Outcome({ outcome }) {
  if (!outcome || outcome.status === "applied") return null;
  const failed = outcome.status === "failed";
  return (
    <div role="status" className="note note-danger space-y-1">
      <p className="font-medium">
        {failed ? "The apply failed" : "The apply was refused"} ({formatDateTime(outcome.ended_at)})
      </p>
      <p dir="auto" className="whitespace-pre-line">
        {outcome.message}
      </p>
      {outcome.details?.length > 0 && (
        <ul className="list-disc ps-5 text-xs">
          {outcome.details.map((detail, index) => (
            <li key={index} dir="auto">
              {detail}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs">No file was written; the suggestion stays accepted. You can correct it or try again.</p>
    </div>
  );
}

function Marks({ suggestion }) {
  const marks = Object.entries(suggestion.mark_names ?? {});
  if (marks.length === 0) return null;
  return (
    <ul className="space-y-1">
      {marks.map(([page, text]) => (
        <li key={page} className="note note-warning p-2">
          <Ltr>{page}</Ltr>: {text}
        </li>
      ))}
    </ul>
  );
}

function MistakeContext({ suggestion, evidence }) {
  return (
    <>
      <Section title={<>The sentence on “<Bidi>{evidence.page.title ?? evidence.page.page_id}</Bidi>”, as the page reads now</>}>
        <Quote quote={evidence.quote} sentence={proposalOf(suggestion).sentence} />
      </Section>
      <Proposal suggestion={suggestion} />
      <Section title="The claim groups the sentence rests on">
        <Groups groups={evidence.groups} />
      </Section>
      <Section title="The page">
        <Pages pages={[evidence.page]} />
      </Section>
    </>
  );
}

function ContradictionContext({ suggestion, evidence }) {
  return (
    <>
      <Section title="What clashes">
        <Bidi as="p" className="card whitespace-pre-line leading-relaxed">
          {proposalOf(suggestion).clash}
        </Bidi>
      </Section>
      <Section title="The two sides">
        <Sides sides={evidence.sides} />
      </Section>
      <Proposal suggestion={suggestion} />
      <Section title="Pages that should show both sides">
        <Pages pages={evidence.pages} />
      </Section>
    </>
  );
}

function Context({ suggestion, evidence }) {
  if (suggestion.kind === "mistake") return <MistakeContext suggestion={suggestion} evidence={evidence} />;
  if (suggestion.kind === "contradiction") return <ContradictionContext suggestion={suggestion} evidence={evidence} />;
  return (
    <>
      <Proposal suggestion={suggestion} />
      <StructuralEvidence evidence={evidence} />
    </>
  );
}

export function Detail({ detail, applies, busy }) {
  const data = detail.data;
  const suggestion = data?.suggestion;
  return (
    <Panel
      title={suggestion ? <Bidi>{suggestion.title || suggestion.kind_name}</Bidi> : "Suggestion"}
      subtitle={
        suggestion && (
          <span className="flex flex-wrap items-center gap-2">
            <span className={`badge ${KIND_BADGES[suggestion.kind] ?? "badge-slate"}`}>{suggestion.kind_name}</span>
            <span className={`badge capitalize ${STATUS_BADGES[suggestion.status]}`}>{suggestion.status_name}</span>
            <span>Raised {formatDateTime(suggestion.raised_at)}</span>
            <Ltr className="text-slate-400">{suggestion.id}</Ltr>
          </span>
        )
      }
    >
      <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(data)} onRetry={detail.reload}>
        {suggestion && (
          <div className="max-w-4xl space-y-6 p-4 text-sm">
            <Marks suggestion={suggestion} />
            {suggestion.status === "accepted" && <Outcome outcome={outcomeFor(applies, suggestion.id)} />}
            {data.changes && <Changes suggestion={suggestion} changes={data.changes} busy={busy} />}
            <Section title="What the reviewer found">
              <Bidi as="p" className="leading-relaxed">
                {suggestion.reason}
              </Bidi>
            </Section>
            <Context suggestion={suggestion} evidence={data.evidence} />
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
