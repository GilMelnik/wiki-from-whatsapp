import { useState } from "react";
import { Bidi, DryRun, formatCount, formatDateTime, groupCount, JOB_STATE_LABELS, Ltr, Panel, PanelState, titleOf, useJobs } from "@shell";
import { ASSIGNMENTS_KEY, PROPOSAL_KINDS, proposalOperation, proposalRowId, STATUS_LABELS } from "./assign.js";
import { NotRunYet, RunAssignButton } from "./AssignmentViews.jsx";

// The proposals of the assignment step for groups that fit no page: a new page, a new label
// or a wider scope. The list column holds every proposal still to decide; one proposal shows,
// in the order the owner decides: why the step proposes it, what it would add or change, the
// groups it is for, and the dry run of the plan change (what accepting it does downstream).
// The decision is in the side column. Accepting opens the plan's own dialog prefilled (the
// owner fixes the id, the category or the text) and saves the plan change and the decision
// together; the groups are then placed by the next assignment run, which is offered here (the
// frame asks before a paid run). Rejected proposals are never made again; they can be put
// back to pending from the list of all proposals.

const SHOWN_GROUPS = 5;
const KIND_BADGES = { page: "badge-emerald", label: "badge-sky", scope: "badge-amber" };
const STATUS_BADGES = { pending: "badge-amber", accepted: "badge-emerald", rejected: "badge-slate" };

function Groups({ groups }) {
  const [all, setAll] = useState(false);
  const shown = all ? groups : groups.slice(0, SHOWN_GROUPS);
  return (
    <div className="space-y-2">
      <ul className="space-y-1">
        {shown.map((group) => (
          <li key={group.group_id} className="card p-2" title={group.group_id}>
            {group.missing ? (
              <span className="text-slate-500">The group is no longer in the groups file.</span>
            ) : (
              <Bidi as="p">{group.claim_text}</Bidi>
            )}
            {group.withheld && <span className="badge badge-amber mt-1">Withheld from the wiki</span>}
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
        Widen the scope of “<Bidi className="font-medium">{target.title_he}</Bidi>”.
      </p>
    ) : (
      <p className="note note-danger">
        The page <Ltr>{proposal.target_id}</Ltr> is no longer in the plan; the proposal can only be rejected.
      </p>
    );
  }
  const check = target.id_check;
  return (
    <p className="flex flex-wrap items-center gap-2">
      <span>Proposed id:</span>
      {proposal.target_id ? <Ltr>{proposal.target_id}</Ltr> : <span className="text-slate-500">none</span>}
      {check && !check.ok && <span className="text-xs text-amber-800">({check.problem} Fix it when accepting.)</span>}
      {proposal.kind === "page" && (
        <span>
          · in the category{" "}
          {target.parent_ok ? (
            <>
              “<Bidi>{titleOf(overview, proposal.parent_id)}</Bidi>”
            </>
          ) : (
            <span className="text-amber-800">that does not exist (choose one when accepting)</span>
          )}
        </span>
      )}
      {proposal.kind === "label" && (
        <span>
          · kind <Ltr>{proposal.label_kind || "—"}</Ltr>
          {!target.kind_known && " (a new kind)"}
        </span>
      )}
    </p>
  );
}

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

/** One proposal, everything needed to decide on it (after an accept, the run it needs on top). */
export function ProposalDetail({ proposal, overview, offer, on }) {
  const pending = proposal.status === "pending";
  return (
    <Panel
      title={<Bidi>{proposal.title_he}</Bidi>}
      subtitle={
        <span className="flex flex-wrap items-center gap-2">
          <span className={`badge ${KIND_BADGES[proposal.kind] ?? "badge-slate"}`}>{PROPOSAL_KINDS[proposal.kind] ?? proposal.kind}</span>
          <span className={`badge ${STATUS_BADGES[proposal.status]}`}>{STATUS_LABELS[proposal.status] ?? proposal.status}</span>
          <span>Proposed by the assignment step</span>
        </span>
      }
    >
      <div className="max-w-4xl space-y-6 p-4 text-sm">
        {offer && <RunOffer offer={offer} onDismiss={on.dismissOffer} />}
        {proposal.reason && (
          <Section title="Why the step proposes it">
            <Bidi as="p" className="leading-relaxed">
              {proposal.reason}
            </Bidi>
          </Section>
        )}
        <Section title="What it would add or change">
          <div className="card space-y-2">
            {proposal.description && <Bidi as="p">{proposal.description}</Bidi>}
            <Target proposal={proposal} overview={overview} />
            {proposal.scope && (
              <div className={proposal.kind === "scope" && proposal.target.scope ? "grid gap-4 md:grid-cols-2" : ""}>
                {proposal.kind === "scope" && proposal.target.scope && (
                  <div>
                    <p className="text-xs text-slate-500">The scope now</p>
                    <Bidi as="p" className="whitespace-pre-line leading-relaxed text-slate-600">
                      {proposal.target.scope}
                    </Bidi>
                  </div>
                )}
                <div>
                  <p className="text-xs text-slate-500">{proposal.kind === "scope" ? "The wider scope" : "The page's scope"}</p>
                  <Bidi as="p" className="whitespace-pre-line leading-relaxed">
                    {proposal.scope}
                  </Bidi>
                </div>
              </div>
            )}
          </div>
        </Section>
        <Section title={`The groups it is for: ${groupCount(proposal.groups.length, formatCount)}`}>
          <Groups groups={proposal.groups} />
        </Section>
        {pending && <DryRun op={proposalOperation(proposal, overview)} overview={overview} title="What accepting it as proposed does to the plan" />}
      </div>
    </Panel>
  );
}

function RunOffer({ offer, onDismiss }) {
  const { stepsByArtifact, latestByStep } = useJobs();
  const step = stepsByArtifact[ASSIGNMENTS_KEY];
  const latest = step ? latestByStep[step.key] : null;
  const ranSince = latest && Date.parse(latest.started_at) >= Date.parse(offer.at);
  return (
    <div className="note note-success space-y-2">
      <p>
        {offer.label}: saved.{" "}
        {ranSince
          ? `The assignment step has run since (${JOB_STATE_LABELS[latest.state] ?? latest.state}); the proposal's groups now show as it placed them.`
          : "The proposal's groups are placed only at the next run of the assignment step: it asks again only about what the change touches (paid, usually cents)."}
      </p>
      <div className="flex flex-wrap gap-2">
        {!ranSince && <RunAssignButton />}
        <button type="button" className="btn btn-sm" onClick={onDismiss}>
          {ranSince ? "Close" : "Not now"}
        </button>
      </div>
    </div>
  );
}

function ProposalLine({ proposal, busy, on }) {
  return (
    <li className="card flex flex-wrap items-center gap-2">
      <span className={`badge ${KIND_BADGES[proposal.kind] ?? "badge-slate"}`}>{PROPOSAL_KINDS[proposal.kind] ?? proposal.kind}</span>
      <Bidi className="min-w-0 flex-1">{proposal.title_he}</Bidi>
      <span className="text-xs text-slate-500">{groupCount(proposal.groups.length, formatCount)}</span>
      {proposal.status === "pending" ? (
        <button type="button" className="btn btn-sm" onClick={() => on.select(proposalRowId(proposal.id))}>
          Open
        </button>
      ) : (
        <>
          <span className={`badge ${STATUS_BADGES[proposal.status]}`}>{STATUS_LABELS[proposal.status]}</span>
          {proposal.status === "rejected" && (
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => on.decide(proposal, "reopen")}>
              Back to pending
            </button>
          )}
        </>
      )}
    </li>
  );
}

/** Every proposal: those to decide (each opens), then the decided ones, folded. */
export function ProposalsView({ overview, proposals, busy, offer, on }) {
  const summary = overview.assignments;
  const items = proposals.data?.items ?? [];
  const pending = items.filter((item) => item.status === "pending");
  const decided = items.filter((item) => item.status !== "pending");
  return (
    <Panel title="Proposals to change the plan" subtitle={summary?.exists ? `From the assignment step, ${formatDateTime(summary.generated_at)}` : "From the assignment step (6b)"}>
      <div className="max-w-4xl space-y-4 p-4 text-sm">
        {offer && <RunOffer offer={offer} onDismiss={on.dismissOffer} />}
        {!summary?.exists ? (
          <NotRunYet />
        ) : (
          <PanelState
            loading={proposals.loading}
            error={proposals.error}
            hasData={Boolean(proposals.data)}
            onRetry={proposals.reload}
            empty={Boolean(proposals.data) && items.length === 0}
            emptyText="The assignment step proposed no changes: it found no shared subject among the groups it did not place."
          >
            <section>
              <h3 className="section-title">To decide</h3>
              {pending.length === 0 ? (
                <p className="text-slate-500">No proposal is waiting for a decision.</p>
              ) : (
                <ul className="space-y-2">
                  {pending.map((proposal) => (
                    <ProposalLine key={proposal.id} proposal={proposal} busy={busy} on={on} />
                  ))}
                </ul>
              )}
            </section>
            {decided.length > 0 && (
              <details className="mt-4">
                <summary className="cursor-pointer text-slate-600">Decided ({formatCount(decided.length)})</summary>
                <ul className="mt-2 space-y-2">
                  {decided.map((proposal) => (
                    <ProposalLine key={proposal.id} proposal={proposal} busy={busy} on={on} />
                  ))}
                </ul>
              </details>
            )}
          </PanelState>
        )}
      </div>
    </Panel>
  );
}
