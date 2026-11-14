import { Bidi, consequenceLines, formatDateTime, Ltr, useArtifactStatus, useJobs, usePlanOverview, useStepRun } from "@shell";

// What an applied suggestion changed (GET /suggestions/{id} → changes): a text correction as
// the sentences before and after on each page; a plan change as what it does downstream, with
// the steps that must follow offered in order (never started by themselves); a contradiction
// also as the group pairs saved as links. A wrong apply is repaired from the backup named here.


/** The steps a plan change needs before the pages show it, offered in order. */
export function FollowUp({ busy }) {
  const { stepsByArtifact, runningByArtifact } = useJobs();
  const { byKey } = useArtifactStatus();
  const run = useStepRun();
  const rows = ["assignments", "pages"].map((key) => ({
    key,
    step: stepsByArtifact[key],
    running: runningByArtifact[key],
    stale: (byKey[key]?.stale_because ?? []).length > 0,
  }));
  return (
    <ol className="space-y-2">
      {rows.map((row, index) => {
        const waitFor = rows.slice(0, index).find((earlier) => earlier.stale || earlier.running);
        const state = row.running ? "Running" : row.stale ? "Out of date" : "Up to date";
        return (
          <li key={row.key} className="flex flex-wrap items-center gap-2">
            <span>
              {index + 1}. {row.step?.title ?? row.key}
            </span>
            <span className={`badge ${row.running ? "badge-sky" : row.stale ? "badge-amber" : "badge-emerald"}`}>{state}</span>
            {row.stale && !row.running && row.step && (
              <button
                type="button"
                className="btn btn-sm"
                disabled={Boolean(waitFor) || busy}
                title={waitFor ? `First ${waitFor.step?.title ?? waitFor.key}` : busy ? "Waits until the background applies are done" : undefined}
                onClick={() => run(row.step.key)}
              >
                {row.step.paid ? "Run…" : "Run"}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function PlanChange({ changes, busy }) {
  const plan = usePlanOverview();
  const lines = consequenceLines(changes.plan, plan.data);
  return (
    <>
      <ul className="space-y-1">
        {lines.map((line) => (
          <li key={line.text} className={`note note-${line.tone} p-2`}>
            {line.text}
          </li>
        ))}
      </ul>
      <section>
        <h4 className="mb-1 text-xs font-medium text-slate-600">So the pages show the change</h4>
        <FollowUp busy={busy} />
      </section>
    </>
  );
}

function Sentences({ runs }) {
  return (
    <ul className="space-y-2">
      {runs.map((run, index) => (
        <li key={index} className="grid gap-2 md:grid-cols-2">
          <div className="note note-danger p-2">
            <p className="mb-1 text-xs">Before</p>
            {run.before.length ? run.before.map((s, i) => <Bidi as="p" key={i}>{s}</Bidi>) : <p className="text-xs opacity-70">(nothing)</p>}
          </div>
          <div className="note note-success p-2">
            <p className="mb-1 text-xs">After</p>
            {run.after.length ? run.after.map((s, i) => <Bidi as="p" key={i}>{s}</Bidi>) : <p className="text-xs opacity-70">(removed)</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function PageChange({ page }) {
  return (
    <li className="space-y-2">
      <p className="flex flex-wrap items-baseline gap-2">
        <Bidi className="font-medium">{page.title}</Bidi>
        <Ltr className="text-xs text-slate-400">{page.page_id}</Ltr>
        {page.kept_sentence_share !== null && page.kept_sentence_share !== undefined && (
          <span className="text-xs text-slate-500">{Math.round(page.kept_sentence_share * 100)}% of the sentences stayed word for word</span>
        )}
      </p>
      {!page.changed ? (
        <p className="text-slate-500">The page already showed both sides and was not changed.</p>
      ) : page.sentences ? (
        page.sentences.length ? <Sentences runs={page.sentences} /> : <p className="text-slate-500">No sentence differs.</p>
      ) : (
        <p className="text-slate-500">The version of the page before or after is no longer available.</p>
      )}
      {page.rewritten_since && <p className="text-xs text-slate-500">The pages file was written again since; “After” is shown as the apply left it.</p>}
    </li>
  );
}

function Links({ links }) {
  return (
    <ul className="space-y-1">
      {links.map(({ a, b }) => (
        <li key={`${a.group_id}-${b.group_id}`} className="card p-2 text-xs">
          <Bidi as="p">{a.claim_text ?? a.group_id}</Bidi>
          <p className="my-1 text-slate-500">⟷ contradicts</p>
          <Bidi as="p">{b.claim_text ?? b.group_id}</Bidi>
        </li>
      ))}
    </ul>
  );
}

export function Changes({ suggestion, changes, busy }) {
  const structural = Boolean(changes.plan);
  return (
    <section className="space-y-3 rounded-md border border-emerald-200 bg-emerald-50/40 p-3">
      <h3 className="section-title mb-0">What the apply changed ({formatDateTime(changes.applied_at)})</h3>
      <p dir="auto">{changes.message}</p>
      {structural ? <PlanChange changes={changes} busy={busy} /> : <ul className="space-y-4">{changes.pages.map((page) => <PageChange key={page.page_id} page={page} />)}</ul>}
      {suggestion.kind === "contradiction" && changes.links?.length > 0 && (
        <section>
          <h4 className="mb-1 text-xs font-medium text-slate-600">The contradiction links saved in the claim groups</h4>
          <Links links={changes.links} />
        </section>
      )}
      <p className="text-xs text-slate-600">
        There is no undo button. A wrong apply is repaired from the backup of the file as it was before:{" "}
        {changes.backup ? <Ltr>{changes.backup}</Ltr> : <>there was no edited copy before, so the earlier version is the step's original; the file written: <Ltr>{changes.file}</Ltr></>}
        {changes.groups_backup && (
          <>
            {" "}· claim groups: <Ltr>{changes.groups_backup}</Ltr>
          </>
        )}
      </p>
    </section>
  );
}
