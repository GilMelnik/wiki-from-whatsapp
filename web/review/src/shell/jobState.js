import { formatDateTime } from "./format.js";

// Pure logic of the step runner UI (no React), so it can be tested with `node --test`:
// what notice a tool shows for one artifact it reads or writes, and how streamed log lines
// are merged into a job's log.

export const RUNNING = "running";
export const SUCCEEDED = "succeeded";
export const FAILED = "failed";
export const CANCELLED = "cancelled";

// What the banner's button does.
export const RUN_ACTION = "run";
export const LOG_ACTION = "log";

export const JOB_STATE_LABELS = {
  [RUNNING]: "Running",
  [SUCCEEDED]: "Done",
  [FAILED]: "Failed",
  [CANCELLED]: "Stopped",
};

export const JOB_STATE_BADGES = {
  [RUNNING]: "badge-sky",
  [SUCCEEDED]: "badge-emerald",
  [FAILED]: "badge-rose",
  [CANCELLED]: "badge-slate",
};

/** The time the artifact's next reader sees as its last change (edited copy first). */
export function effectiveMtime(artifact) {
  return artifact?.edited_mtime ?? artifact?.mtime ?? null;
}

/** Steps by key and by the artifact they produce. */
export function indexSteps(steps) {
  return {
    byKey: Object.fromEntries(steps.map((step) => [step.key, step])),
    byArtifact: Object.fromEntries(steps.map((step) => [step.produces, step])),
  };
}

/** From jobs (newest first): the newest job per step and the running job per produced artifact. */
export function indexJobs(jobs) {
  const latestByStep = {};
  const runningByArtifact = {};
  for (const job of jobs) {
    latestByStep[job.step] ??= job;
    if (job.state === RUNNING) runningByArtifact[job.produces] ??= job;
  }
  return { latestByStep, runningByArtifact };
}

function changedInputs(artifact, byKey) {
  const keys = artifact.stale_because;
  const parts = keys.map((key) => {
    const upstream = byKey[key];
    return `“${upstream?.title ?? key}” (${formatDateTime(effectiveMtime(upstream))})`;
  });
  return parts.join(", ");
}

function newestInputChange(artifact, byKey) {
  const times = artifact.stale_because.map((key) => Date.parse(effectiveMtime(byKey[key]) ?? ""));
  return Math.max(...times.filter((time) => !Number.isNaN(time)));
}

/**
 * The notice for one artifact a tool reads and/or writes (`writes`), or null. `running`: the
 * job rewriting it; `feeding`: a running job that rewrites one of its inputs; `latest`: the
 * newest job of its step. `quiet`: the tool says itself that the artifact is out of date (a
 * paid step the owner runs when ready), so only "running", "wait for the input" and "the last
 * run failed" are shown. Returns {tone, text, action: RUN_ACTION | LOG_ACTION | null, actionLabel, jobId?}.
 */
export function artifactNotice({ artifact, byKey, step, running, feeding, latest, writes, quiet = false }) {
  if (!artifact) return null;
  if (running) {
    const effect = writes ? " Saving here is blocked until it finishes; the view then updates by itself." : "";
    return {
      tone: "info",
      text: `${running.title} is running and rewriting “${artifact.title}”.${effect}`,
      action: LOG_ACTION,
      actionLabel: "Show log",
      jobId: running.id,
    };
  }
  if (!step || artifact.stale_because.length === 0) return null;
  const stale = `“${artifact.title}” is out of date: ${changedInputs(artifact, byKey)} changed since it was made.`;
  if (feeding) {
    return {
      tone: "info",
      text: `${stale} Wait for ${feeding.title} to finish, then run ${step.title}.`,
      action: LOG_ACTION,
      actionLabel: "Show log",
      jobId: feeding.id,
    };
  }
  const failedSinceChange =
    latest?.state === FAILED && Date.parse(latest.started_at) >= newestInputChange(artifact, byKey);
  if (failedSinceChange) {
    return {
      tone: "danger",
      text: `${stale} The last run of ${step.title} failed (exit code ${latest.exit_code}).`,
      action: LOG_ACTION,
      actionLabel: "Show log",
      jobId: latest.id,
    };
  }
  if (quiet) return null;
  if (!step.incremental_safe) {
    const how = step.command ? `: ${step.command}` : "";
    return {
      tone: "warning",
      text: `${stale} ${step.title} cannot run from here yet (it would not keep manual edits); run it by hand${how}.`,
      action: null,
      actionLabel: null,
    };
  }
  return {
    tone: "warning",
    text: `${stale} ${step.title} updates it, keeping manual edits.`,
    action: RUN_ACTION,
    actionLabel: step.paid ? "Run step…" : "Run step",
  };
}

/**
 * Merge a `job_progress` payload ({from, lines}) into a log ({start, lines, limit}).
 * Returns the new log, the same log when the payload adds nothing new, or null when lines
 * were missed (the caller refetches the job).
 */
export function appendProgress(log, payload) {
  const end = log.start + log.lines.length;
  if (payload.from > end) return null;
  const fresh = payload.lines.slice(end - payload.from);
  if (fresh.length === 0) return log;
  const lines = [...log.lines, ...fresh];
  const drop = Math.max(0, lines.length - log.limit);
  return { ...log, start: log.start + drop, lines: drop ? lines.slice(drop) : lines };
}
