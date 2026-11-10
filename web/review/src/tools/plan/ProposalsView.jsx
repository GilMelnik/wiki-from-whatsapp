import { useState } from "react";
import { Bidi, formatCount, formatDateTime, JOB_STATE_LABELS, Ltr, Panel, PanelState, useJobs, useResource } from "@shell";
import { api } from "./api.js";
import { ASSIGNMENTS_KEY, groupCount, PROPOSAL_KINDS, STATUS_LABELS } from "./assign.js";
import { NotRunYet, RunAssignButton } from "./AssignmentViews.jsx";
import { titleOf } from "./plan.js";

// The proposals of the assignment step for groups that fit no page: a new page, a new label
// or a wider scope. Pending ones first, each with its reason and its groups' texts; decided
// ones folded away. Accepting opens the plan's own dialog prefilled (the owner fixes the id,
// the category or the text) and saves the plan change and the decision together; the groups
// are then placed by the next assignment run, which this view offers (the frame asks before
// a paid run). Rejected proposals are never made again; they can be put back to pending.

const SHOWN_GROUPS = 5;
const KIND_BADGES = { page: "badge-emerald", label: "badge-sky", scope: "badge-amber" };
const STATUS_BADGES = { pending: "badge-amber", accepted: "badge-emerald", rejected: "badge-slate" };

function Groups({ groups }) {
  const [all, setAll] = useState(false);
  const shown = all ? groups : groups.slice(0, SHOWN_GROUPS);
  return (
    <div className="space-y-1">
      <ul className="space-y-1">
        {shown.map((group) => (
          <li key={group.group_id} className="rounded border border-slate-200 bg-white p-2 text-xs">
            {group.missing ? <span className="text-slate-400">הקבוצה כבר לא בקובץ הקבוצות.</span> : <Bidi as="p">{group.claim_text}</Bidi>}
            <Ltr className="text-slate-400">{group.group_id}</Ltr>
          </li>
        ))}
      </ul>
      {groups.length > SHOWN_GROUPS && (
        <button type="button" className="btn btn-sm" onClick={() => setAll(!all)}>
          {all ? "פחות" : `עוד ${formatCount(groups.length - SHOWN_GROUPS)}`}
        </button>
      )}
    </div>
  );
}

function Target({ proposal, overview }) {
  const { target } = proposal;
  if (proposal.kind === "scope") {
    return target.page_id ? (
      <p>
        הרחבת התחום של <Bidi className="font-medium">„{target.title_he}”</Bidi> <Ltr className="text-xs text-slate-400">{target.page_id}</Ltr>
      </p>
    ) : (
      <p className="text-rose-700">הדף <Ltr>{proposal.target_id}</Ltr> כבר לא בתוכנית; אפשר רק לדחות.</p>
    );
  }
  const check = target.id_check;
  return (
    <p className="flex flex-wrap items-center gap-2">
      <span>מזהה מוצע:</span>
      {proposal.target_id ? <Ltr>{proposal.target_id}</Ltr> : <span className="text-slate-400">אין</span>}
      {check && !check.ok && <span className="text-xs text-amber-800">({check.problem} תקנו בחלון הקבלה.)</span>}
      {proposal.kind === "page" && (
        <span>
          · בקטגוריה{" "}
          {target.parent_ok ? <Bidi>„{titleOf(overview, proposal.parent_id)}”</Bidi> : <span className="text-amber-800">שלא קיימת (תבחרו בחלון)</span>}
        </span>
      )}
      {proposal.kind === "label" && <span>· סוג <Ltr>{proposal.label_kind || "—"}</Ltr>{!target.kind_known && " (סוג חדש)"}</span>}
    </p>
  );
}

function ProposalCard({ proposal, overview, busy, on }) {
  const pending = proposal.status === "pending";
  return (
    <article className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <header className="flex flex-wrap items-center gap-2">
        <span className={`badge ${KIND_BADGES[proposal.kind] ?? "badge-slate"}`}>{PROPOSAL_KINDS[proposal.kind] ?? proposal.kind}</span>
        <Bidi as="h3" className="text-base font-semibold">{proposal.title_he}</Bidi>
        <span className={`badge ${STATUS_BADGES[proposal.status]}`}>{STATUS_LABELS[proposal.status] ?? proposal.status}</span>
        <Ltr className="text-xs text-slate-400">{proposal.id}</Ltr>
      </header>
      {proposal.description && <Bidi as="p">{proposal.description}</Bidi>}
      {proposal.reason && (
        <p className="text-slate-600">
          למה: <Bidi>{proposal.reason}</Bidi>
        </p>
      )}
      <Target proposal={proposal} overview={overview} />
      {proposal.scope && (
        <details>
          <summary className="cursor-pointer text-xs text-slate-600">{proposal.kind === "scope" ? "התחום המוצע" : "התחום של הדף"}</summary>
          <Bidi as="p" className="mt-1 whitespace-pre-line rounded bg-slate-50 p-2 leading-relaxed">{proposal.scope}</Bidi>
        </details>
      )}
      <section>
        <h4 className="section-title">הקבוצות שההצעה נועדה להן: {groupCount(proposal.groups.length, formatCount)}</h4>
        <Groups groups={proposal.groups} />
      </section>
      <footer className="flex flex-wrap gap-2">
        {pending && (
          <>
            <button type="button" className="btn btn-sm btn-primary" disabled={busy || (proposal.kind === "scope" && !proposal.target.page_id)} onClick={() => on.openAccept(proposal)}>
              קבלה…
            </button>
            <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => on.decide(proposal, "reject")}>
              דחייה
            </button>
          </>
        )}
        {proposal.status === "rejected" && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => on.decide(proposal, "reopen")}>
            החזרה להמתנה
          </button>
        )}
      </footer>
    </article>
  );
}

function RunOffer({ offer, onDismiss }) {
  const { stepsByArtifact, latestByStep } = useJobs();
  const step = stepsByArtifact[ASSIGNMENTS_KEY];
  const latest = step ? latestByStep[step.key] : null;
  const ranSince = latest && Date.parse(latest.started_at) >= Date.parse(offer.at);
  return (
    <div className="space-y-2 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-emerald-900">
      <p>
        <Bidi>{offer.label}</Bidi> — נשמר.{" "}
        {ranSince
          ? `שלב השיוך הורץ מאז (${JOB_STATE_LABELS[latest.state] ?? latest.state}); הקבוצות של ההצעה מופיעות עכשיו לפי התוצאה שלו.`
          : "הקבוצות של ההצעה ישובצו רק בהרצה הבאה של שלב השיוך: הוא שואל שוב רק את מה שהשינוי נוגע בו (בתשלום, בדרך כלל סנטים)."}
      </p>
      <div className="flex flex-wrap gap-2">
        {!ranSince && <RunAssignButton />}
        <button type="button" className="btn btn-sm" onClick={onDismiss}>{ranSince ? "סגירה" : "לא עכשיו"}</button>
      </div>
    </div>
  );
}

export function ProposalsView({ overview, watch, busy, offer, on }) {
  const summary = overview.assignments;
  const listing = useResource(() => (summary?.exists ? api.get("/proposals") : null), [summary?.exists], { watch });
  const items = listing.data?.items ?? [];
  const pending = items.filter((item) => item.status === "pending");
  const decided = items.filter((item) => item.status !== "pending");
  const counts = summary?.proposals;
  return (
    <Panel
      title="הצעות לשינוי התוכנית"
      subtitle={
        counts
          ? `${formatCount(counts.pending)} ממתינות · ${formatCount(counts.accepted)} התקבלו · ${formatCount(counts.rejected)} נדחו · משלב השיוך, ${formatDateTime(summary.generated_at)}`
          : "משלב השיוך (6ב)"
      }
    >
      <div className="space-y-4 p-4 text-sm">
        {offer && <RunOffer offer={offer} onDismiss={on.dismissOffer} />}
        {!summary?.exists ? (
          <NotRunYet />
        ) : (
          <PanelState loading={listing.loading} error={listing.error} hasData={Boolean(listing.data)} onRetry={listing.reload} empty={Boolean(listing.data) && items.length === 0} emptyText="שלב השיוך לא הציע שינויים: כל קבוצה שלא שובצה נראתה לו בלי נושא משותף.">
            {pending.length === 0 && <p className="text-slate-500">אין הצעות שממתינות להחלטה.</p>}
            {pending.map((proposal) => (
              <ProposalCard key={proposal.id} proposal={proposal} overview={overview} busy={busy} on={on} />
            ))}
            {decided.length > 0 && (
              <details className="rounded border border-slate-200 p-3">
                <summary className="cursor-pointer text-slate-600">הוחלטו ({formatCount(decided.length)})</summary>
                <div className="mt-3 space-y-3">
                  {decided.map((proposal) => (
                    <ProposalCard key={proposal.id} proposal={proposal} overview={overview} busy={busy} on={on} />
                  ))}
                </div>
              </details>
            )}
          </PanelState>
        )}
      </div>
    </Panel>
  );
}
