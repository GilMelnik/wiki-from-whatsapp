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
  [RUNNING]: "רץ",
  [SUCCEEDED]: "הסתיים",
  [FAILED]: "נכשל",
  [CANCELLED]: "בוטל",
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
    return `"${upstream?.title ?? key}" (${formatDateTime(effectiveMtime(upstream))})`;
  });
  return `${parts.join(", ")} ${keys.length === 1 ? "השתנה" : "השתנו"}`;
}

function newestInputChange(artifact, byKey) {
  const times = artifact.stale_because.map((key) => Date.parse(effectiveMtime(byKey[key]) ?? ""));
  return Math.max(...times.filter((time) => !Number.isNaN(time)));
}

/**
 * The notice for one artifact a tool reads (`reads`) and/or writes (`writes`), or null.
 * Returns {tone, text, action: RUN_ACTION | LOG_ACTION | null, actionLabel, jobId?}.
 */
export function artifactNotice({ artifact, byKey, step, running, latest, writes }) {
  if (!artifact) return null;
  if (running) {
    const effect = writes
      ? "שמירת שינויים בכלי הזה חסומה עד שההרצה תסתיים; התצוגה תתעדכן מעצמה."
      : "התצוגה תתעדכן מעצמה כשההרצה תסתיים.";
    return {
      tone: "info",
      text: `"${running.title}" רץ כעת ומעדכן את "${artifact.title}". ${effect}`,
      action: LOG_ACTION,
      actionLabel: "יומן ההרצה",
      jobId: running.id,
    };
  }
  if (!step || artifact.stale_because.length === 0) return null;
  const stale = `${changedInputs(artifact, byKey)} אחרי ש"${artifact.title}" נוצר, ולכן מה שמוצג כאן עוד לא משקף את השינוי.`;
  const failedSinceChange =
    latest?.state === FAILED && Date.parse(latest.started_at) >= newestInputChange(artifact, byKey);
  if (failedSinceChange) {
    return {
      tone: "danger",
      text: `${stale} ההרצה האחרונה של "${step.title}" נכשלה (קוד יציאה ${latest.exit_code}).`,
      action: LOG_ACTION,
      actionLabel: "יומן ההרצה",
      jobId: latest.id,
    };
  }
  if (!step.incremental_safe) {
    const how = step.command ? `: ${step.command}` : "";
    return {
      tone: "warning",
      text: `${stale} את "${step.title}" אי אפשר עדיין להריץ מכאן (הוא לא שומר על עריכות ידניות); הריצו אותו ידנית${how}.`,
      action: null,
      actionLabel: null,
    };
  }
  return {
    tone: "warning",
    text: `${stale} הרצה מחדש של "${step.title}" תעדכן אותו: רק מה שהשתנה מעובד, ועריכות ידניות נשמרות.`,
    action: RUN_ACTION,
    actionLabel: step.paid ? "הרצת השלב…" : "הרצת השלב",
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
