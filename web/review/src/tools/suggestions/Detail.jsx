import { Bidi, formatDateTime, Ltr, Panel, PanelState } from "@shell";
import { Changes } from "./Changes.jsx";
import { Evidence } from "./Evidence.jsx";
import { Proposal } from "./Proposal.jsx";
import { KIND_BADGES, outcomeFor, proposalOf, STATUS_BADGES } from "./suggestions.js";

// The main column for one suggestion: what is wrong (the reviewer's reason), the evidence
// from the wiki as it is now, the proposal, the last failed attempt to carry it out, and once
// applied what it changed.

export function Outcome({ outcome }) {
  if (!outcome || outcome.status === "applied") return null;
  const failed = outcome.status === "failed";
  return (
    <div role="status" className="space-y-1 rounded border border-rose-200 bg-rose-50 p-3 text-rose-900">
      <p className="font-medium">{failed ? "הביצוע נכשל" : "הביצוע סורב"} ({formatDateTime(outcome.ended_at)})</p>
      <Bidi as="p" className="whitespace-pre-line">
        {outcome.message}
      </Bidi>
      {outcome.details?.length > 0 && (
        <ul className="list-disc ps-5 text-xs">
          {outcome.details.map((detail, index) => (
            <li key={index} dir="auto">
              {detail}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs">שום קובץ לא נכתב; ההצעה נשארה מקובלת, ואפשר לתקן אותה או לנסות שוב.</p>
    </div>
  );
}

function Marks({ suggestion }) {
  const marks = Object.entries(suggestion.mark_names ?? {});
  if (marks.length === 0) return null;
  return (
    <ul className="space-y-1">
      {marks.map(([page, text]) => (
        <li key={page} className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-900">
          <Ltr>{page}</Ltr>: {text}
        </li>
      ))}
    </ul>
  );
}

export function Detail({ detail, applies, busy }) {
  const data = detail.data;
  const suggestion = data?.suggestion;
  return (
    <Panel
      title={suggestion ? <Bidi>{suggestion.title || suggestion.kind_name}</Bidi> : "הצעה"}
      subtitle={
        suggestion && (
          <span className="flex flex-wrap items-center gap-1">
            <span className={`badge ${KIND_BADGES[suggestion.kind] ?? "badge-slate"}`}>{suggestion.kind_name}</span>
            <span className={`badge ${STATUS_BADGES[suggestion.status]}`}>{suggestion.status_name}</span>
            <Ltr className="text-slate-400">{suggestion.id}</Ltr>
            <span>הועלתה {formatDateTime(suggestion.raised_at)}</span>
          </span>
        )
      }
    >
      <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(data)} onRetry={detail.reload}>
        {suggestion && (
          <div className="space-y-6 p-4 text-sm">
            <Marks suggestion={suggestion} />
            <section>
              <h3 className="section-title">מה לא בסדר</h3>
              <Bidi as="p" className="leading-relaxed">
                {suggestion.reason}
              </Bidi>
            </section>
            {suggestion.status === "accepted" && <Outcome outcome={outcomeFor(applies, suggestion.id)} />}
            {data.changes && <Changes suggestion={suggestion} changes={data.changes} busy={busy} />}
            <Proposal suggestion={suggestion} />
            <Evidence suggestion={suggestion} evidence={data.evidence} proposal={proposalOf(suggestion)} />
          </div>
        )}
      </PanelState>
    </Panel>
  );
}
