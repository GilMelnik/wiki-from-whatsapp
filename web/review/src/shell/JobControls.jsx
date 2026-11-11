import { useEffect, useRef } from "react";
import { Dialog } from "./Dialog.jsx";
import { useEvents } from "./events.js";
import { formatCount, formatDateTime } from "./format.js";
import { FAILED, JOB_STATE_BADGES, JOB_STATE_LABELS, RUNNING, SUCCEEDED } from "./jobState.js";
import {
  askToRun,
  cancelJob,
  closeConfirm,
  closeJobPanel,
  openJobPanel,
  selectPanelJob,
  startJob,
  useJobLog,
  useJobs,
  useJobUi,
} from "./jobs.js";
import { useArtifactStatus } from "./serverState.js";
import { Ltr } from "./text.jsx";
import { useToast } from "./Toast.jsx";

// The visible parts of the step runner: the header indicator, the job panel with the log,
// the confirmation before a paid run or a rebuild, and `useStepRun()` for any run button.

/**
 * `run(stepKey, {rebuild})`: starts the step, after a confirmation dialog when the step makes
 * paid API calls or `rebuild` is set (a rebuild discards manual edits: never offer it from a
 * banner). Failures are shown as a toast.
 */
export function useStepRun() {
  const toast = useToast();
  const { stepsByKey } = useJobs();
  return (stepKey, { rebuild = false } = {}) => {
    const step = stepsByKey[stepKey];
    if (!step) return;
    if (step.paid || rebuild) {
      closeJobPanel(); // one dialog at a time; the panel reopens on the new job
      askToRun({ step, rebuild });
      return;
    }
    startJob(stepKey).then(
      (job) => toast.info(`${job.title} started`),
      (error) => toast.error(error)
    );
  };
}

function JobBadge({ job }) {
  return <span className={`badge ${JOB_STATE_BADGES[job.state]}`}>{JOB_STATE_LABELS[job.state]}</span>;
}

/**
 * Header: a running step (pulsing) or a failed last run stays in view; a finished run is
 * already told by its toast, so then only a quiet "Runs" button opens the panel with the logs.
 */
export function JobIndicator() {
  const { jobs } = useJobs();
  const newest = jobs[0];
  if (!newest) return null;
  const running = jobs.filter((job) => job.state === RUNNING);
  const shown = running[0] ?? newest;
  const loud = shown.state === RUNNING || shown.state === FAILED;
  return (
    <button
      type="button"
      className={`btn btn-sm gap-2 ${loud ? "" : "btn-quiet"}`}
      title="Step runs: state and log"
      onClick={() => openJobPanel(shown.id)}
    >
      {shown.state === RUNNING && <span aria-hidden="true" className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />}
      {loud ? (
        <>
          <span>{shown.title}</span>
          <JobBadge job={shown} />
          {running.length > 1 && <span className="text-slate-500">+{running.length - 1}</span>}
        </>
      ) : (
        "Runs"
      )}
    </button>
  );
}

/** A job's state line, highlighted lines (superseded manual edits) and the log, following the stream. */
export function JobLog({ jobId, className = "max-h-[50vh]" }) {
  const { job, log, error, loading } = useJobLog(jobId);
  const toast = useToast();
  const scrollRef = useRef(null);
  const atBottom = useRef(true);
  const lineCount = log ? log.start + log.lines.length : 0;

  useEffect(() => {
    const element = scrollRef.current;
    if (element && atBottom.current) element.scrollTop = element.scrollHeight;
  }, [lineCount]);

  if (error) return <p dir="auto" className="text-rose-700">{error.message}</p>;
  if (!job) return <p className="text-slate-400">{loading ? "Loading…" : "No run to show"}</p>;
  const cancel = () => cancelJob(job.id).then(() => toast.info("Asked the run to stop"), (failure) => toast.error(failure));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{job.title}</span>
        <JobBadge job={job} />
        {job.rebuild && <span className="badge badge-rose">Rebuild</span>}
        <span className="text-xs text-slate-500">
          {formatDateTime(job.started_at)}
          {job.ended_at && ` – ${formatDateTime(job.ended_at)}`}
          {job.exit_code !== null && <> · exit code <Ltr>{job.exit_code}</Ltr></>}
        </span>
        {job.state === RUNNING && (
          <button type="button" className="btn btn-sm ms-auto" disabled={job.cancel_requested} onClick={cancel}>
            {job.cancel_requested ? "Stopping…" : "Stop"}
          </button>
        )}
      </div>
      {job.highlights.length > 0 && (
        <div role="alert" className="note note-warning">
          <p className="mb-1 font-medium">Manual edits were replaced by a new result (they are kept in the backup):</p>
          {job.highlights.map((line, index) => (
            <Ltr key={index} className="block whitespace-pre-wrap break-all text-xs">
              {line}
            </Ltr>
          ))}
        </div>
      )}
      <pre
        ref={scrollRef}
        dir="ltr"
        onScroll={(event) => {
          const element = event.currentTarget;
          atBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        }}
        className={`overflow-auto whitespace-pre-wrap break-all rounded-md border border-slate-200 bg-slate-100 p-3 text-start text-xs text-slate-800 ${className}`}
      >
        {log.start > 0 && <span className="text-slate-500">{`… ${formatCount(log.start)} earlier lines not shown\n`}</span>}
        {log.lines.length > 0 ? log.lines.join("\n") : "(no output yet)"}
      </pre>
    </div>
  );
}

function JobPanel({ selectedId }) {
  const { jobs, stepsByKey } = useJobs();
  const run = useStepRun();
  const selected = jobs.find((job) => job.id === selectedId) ?? jobs[0];
  if (!selected) return <p className="text-slate-400">No step has run from this server yet.</p>;
  const jobId = selected.id;
  const step = stepsByKey[selected.step];
  const runAgain = selected.state !== RUNNING && step?.incremental_safe;
  return (
    <div className="space-y-4">
      {runAgain && (
        <button type="button" className="btn btn-sm" onClick={() => run(step.key)}>
          Run {step.title} again
        </button>
      )}
      {jobs.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {jobs.map((job) => (
            <button
              key={job.id}
              type="button"
              aria-pressed={job.id === jobId}
              className={`btn btn-sm gap-1 ${job.id === jobId ? "border-blue-500 bg-blue-50" : ""}`}
              onClick={() => selectPanelJob(job.id)}
            >
              {job.title} <JobBadge job={job} />
            </button>
          ))}
        </div>
      )}
      <JobLog jobId={jobId} />
    </div>
  );
}

function ConfirmRun({ request }) {
  const { byKey } = useArtifactStatus();
  const toast = useToast();
  const { step, rebuild } = request ?? {};
  const artifact = step ? byKey[step.produces] : null;
  const changed = artifact?.stale_because ?? [];
  return (
    <Dialog
      open={Boolean(request)}
      title={rebuild ? `Rebuild: ${step?.title}` : `Run ${step?.title}`}
      confirmLabel={rebuild ? "Rebuild" : "Run"}
      tone={rebuild ? "danger" : "primary"}
      onClose={closeConfirm}
      onConfirm={async () => {
        const job = await startJob(step.key, { rebuild });
        toast.info(`${job.title} started`);
        openJobPanel(job.id);
      }}
    >
      <div className="space-y-3">
        {rebuild && (
          <p className="note note-danger">
            A rebuild processes every item from the start and replaces the edited copy. Manual edits will not stay in the
            file (the edited copy is backed up first).
          </p>
        )}
        {step?.paid && <p>This step makes paid API calls{rebuild ? " for every item" : ", only for items that are new or changed"}.</p>}
        {changed.length > 0 && (
          <div>
            <p>Files changed since the last run:</p>
            <ul className="ms-5 list-disc">
              {changed.map((key) => (
                <li key={key}>
                  {byKey[key]?.title ?? key} ({formatDateTime(byKey[key]?.edited_mtime ?? byKey[key]?.mtime)})
                </li>
              ))}
            </ul>
          </div>
        )}
        {step?.command && (
          <p className="text-xs text-slate-500">
            Command: <Ltr>{rebuild ? step.rebuild_command : step.command}</Ltr>
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Rendered once by the shell: the job panel, the run confirmation, and a toast per finished job. */
export function JobHost() {
  const ui = useJobUi();
  const toast = useToast();
  useEvents("job_finished", ({ payload }) => {
    if (payload.state === SUCCEEDED) toast.success(`${payload.title} finished`);
    else if (payload.state === FAILED) toast.error(`${payload.title} failed (exit code ${payload.exit_code})`);
    else toast.info(`${payload.title} was stopped`);
  });
  return (
    <>
      <Dialog open={ui.panelOpen} title="Step runs" size="lg" onClose={closeJobPanel}>
        <JobPanel selectedId={ui.panelJobId} />
      </Dialog>
      <ConfirmRun request={ui.confirm} />
    </>
  );
}
