import { useState } from "react";
import { Bidi, formatCount, formatDateTime, groupCount, JOB_STATE_LABELS, Ltr, Panel, PanelState, titleOf, useJobs, useResource } from "@shell";
import { api } from "./api.js";
import { ASSIGNMENTS_KEY, PROPOSAL_KINDS, STATUS_LABELS } from "./assign.js";
import { NotRunYet, RunAssignButton } from "./AssignmentViews.jsx";

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
            {group.missing ? <span className="text-slate-400">The group is no longer in the groups file.</span> : <Bidi as="p">{group.claim_text}</Bidi>}
            <Ltr className="text-slate-400">{group.group_id}</Ltr>
          </li>
        ))}
      </ul>
      {groups.length > SHOWN_GROUPS && (
        <button type="button" className="btn btn-sm" onClick={() => setAll(!all)}>
          {all ? "Fewer" : `${formatCount(groups.length - SHOWN_GROUPS)} more`}
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
        Widen the scope of “<Bidi className="font-medium">{target.title_he}</Bidi>” <Ltr className="text-xs text-slate-400">{target.page_id}</Ltr>
      </p>
    ) : (
      <p className="text-rose-700">The page <Ltr>{proposal.target_id}</Ltr> is no longer in the plan; the proposal can only be rejected.</p>
    );
  }
  const check = target.id_check;
  return (
    <p className="flex flex-wrap items-center gap-2">
      <span>Proposed id:</span>
      {proposal.target_id ? <Ltr>{proposal.target_id}</Ltr> : <span className="text-slate-400">none</span>}
      {check && !check.ok && <span className="text-xs text-amber-800">({check.problem} Fix it in the accept window.)</span>}
      {proposal.kind === "page" && (
        <span>
          · in the category{" "}
          {target.parent_ok ? <>“<Bidi>{titleOf(overview, proposal.parent_id)}</Bidi>”</> : <span className="text-amber-800">that does not exist (choose one in the window)</span>}
        </span>
      )}
      {proposal.kind === "label" && <span>· kind <Ltr>{proposal.label_kind || "—"}</Ltr>{!target.kind_known && " (a new kind)"}</span>}
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
          Why: <Bidi>{proposal.reason}</Bidi>
        </p>
      )}
      <Target proposal={proposal} overview={overview} />
      {proposal.scope && (
        <details>
          <summary className="cursor-pointer text-xs text-slate-600">{proposal.kind === "scope" ? "Proposed scope" : "The page's scope"}</summary>
          <Bidi as="p" className="mt-1 whitespace-pre-line rounded bg-slate-50 p-2 leading-relaxed">{proposal.scope}</Bidi>
        </details>
      )}
      <section>
        <h4 className="section-title">The groups the proposal is for: {groupCount(proposal.groups.length, formatCount)}</h4>
        <Groups groups={proposal.groups} />
      </section>
      <footer className="flex flex-wrap gap-2">
        {pending && (
          <>
            <button type="button" className="btn btn-sm btn-primary" disabled={busy || (proposal.kind === "scope" && !proposal.target.page_id)} onClick={() => on.openAccept(proposal)}>
              Accept…
            </button>
            <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => on.decide(proposal, "reject")}>
              Reject
            </button>
          </>
        )}
        {proposal.status === "rejected" && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => on.decide(proposal, "reopen")}>
            Back to pending
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
        {offer.label}: saved.{" "}
        {ranSince
          ? `The assignment step has run since (${JOB_STATE_LABELS[latest.state] ?? latest.state}); the proposal's groups now show as it placed them.`
          : "The proposal's groups are placed only at the next run of the assignment step: it asks again only about what the change touches (paid, usually cents)."}
      </p>
      <div className="flex flex-wrap gap-2">
        {!ranSince && <RunAssignButton />}
        <button type="button" className="btn btn-sm" onClick={onDismiss}>{ranSince ? "Close" : "Not now"}</button>
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
      title="Proposals to change the plan"
      subtitle={
        counts
          ? `${formatCount(counts.pending)} pending · ${formatCount(counts.accepted)} accepted · ${formatCount(counts.rejected)} rejected · from the assignment step, ${formatDateTime(summary.generated_at)}`
          : "From the assignment step (6b)"
      }
    >
      <div className="space-y-4 p-4 text-sm">
        {offer && <RunOffer offer={offer} onDismiss={on.dismissOffer} />}
        {!summary?.exists ? (
          <NotRunYet />
        ) : (
          <PanelState loading={listing.loading} error={listing.error} hasData={Boolean(listing.data)} onRetry={listing.reload} empty={Boolean(listing.data) && items.length === 0} emptyText="The assignment step proposed no changes: it found no shared subject among the groups it did not place.">
            {pending.length === 0 && <p className="text-slate-500">No proposals are waiting for a decision.</p>}
            {pending.map((proposal) => (
              <ProposalCard key={proposal.id} proposal={proposal} overview={overview} busy={busy} on={on} />
            ))}
            {decided.length > 0 && (
              <details className="rounded border border-slate-200 p-3">
                <summary className="cursor-pointer text-slate-600">Decided ({formatCount(decided.length)})</summary>
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
