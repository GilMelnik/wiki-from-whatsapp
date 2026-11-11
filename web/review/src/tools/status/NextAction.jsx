import { formatDateTime, JobLog, Ltr, useStepRun } from "@shell";

// What the owner decides on the status tool, for the selected artifact: does its step need to
// run, can it run from here, what will it cost. One note says why (which input changed and
// when, the failed run, the running one), with the buttons that act on it.

function changedInputs(artifact, byKey) {
  return artifact.stale_because
    .map((key) => {
      const input = byKey[key];
      return `“${input?.title ?? key}” (${formatDateTime(input?.edited_mtime ?? input?.mtime)})`;
    })
    .join(", ");
}

function why({ artifact, state, step, running, latest, byKey }) {
  if (state.kind === "running") {
    return { tone: "info", text: `${running.title} is running and rewriting this file. The view updates by itself when it finishes.` };
  }
  if (state.kind === "failed") {
    return { tone: "danger", text: `${changedInputs(artifact, byKey)} changed after this was made, and the last run of ${step.title} failed (exit code ${latest.exit_code}).` };
  }
  if (state.kind === "stale") {
    return { tone: "warning", text: `${changedInputs(artifact, byKey)} changed after this was made, so it does not reflect that yet.` };
  }
  if (state.kind === "missing") {
    return { tone: "plain", text: step ? `There is no file yet: ${step.title} makes it.` : "There is no file yet. No step here makes it; it comes from outside the pipeline." };
  }
  if (state.kind === "input") return { tone: "plain", text: "An input of the pipeline: no step here makes it." };
  return { tone: "success", text: "Up to date with everything it is made from. Nothing to run." };
}

function RunButtons({ step, state, running }) {
  const run = useStepRun();
  if (!step.incremental_safe) {
    return (
      <p className="text-slate-700">
        {step.title} cannot run from here yet: it does not keep manual edits when it runs again.
        {step.command && (
          <>
            {" "}Run it by hand: <Ltr>{step.command}</Ltr>
          </>
        )}
      </p>
    );
  }
  const needed = state.kind === "stale" || state.kind === "failed" || state.kind === "missing";
  return (
    <div className="space-y-2">
      {needed && (
        <p className="text-slate-700">
          Running {step.title} processes only what changed and keeps your edits.
          {step.paid && " It makes paid API calls for the changed items."}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={`btn ${needed ? "btn-primary" : ""}`} disabled={Boolean(running)} onClick={() => run(step.key)}>
          {step.paid ? "Run step…" : "Run step"}
        </button>
        {step.paid && <span className="badge badge-amber">Paid</span>}
        <button type="button" className="btn btn-sm btn-quiet ms-auto text-rose-700" disabled={Boolean(running)} onClick={() => run(step.key, { rebuild: true })}>
          Rebuild from scratch…
        </button>
      </div>
    </div>
  );
}

export function NextAction({ artifact, state, step, running, latest, byKey }) {
  const note = why({ artifact, state, step, running, latest, byKey });
  const logId = running?.id ?? (state.kind === "failed" ? latest.id : null);
  return (
    <section className="space-y-3">
      <p className={`note note-${note.tone}`}>{note.text}</p>
      {step && state.kind !== "running" && <RunButtons step={step} state={state} running={running} />}
      {logId && <JobLog jobId={logId} className="max-h-72" />}
    </section>
  );
}
