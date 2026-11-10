import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createApi } from "./api.js";
import { channel, useEvents } from "./events.js";
import { appendProgress, indexJobs, indexSteps } from "./jobState.js";
import { createLatestGuard, useLatest } from "./latest.js";

// The step runner, client side. Server: GET /api/steps (the step registry), GET /api/jobs,
// POST /api/jobs {step, rebuild}, GET /api/jobs/{id}, POST /api/jobs/{id}/cancel. Events:
// job_started / job_finished (payload = job summary), job_progress (payload = {id, from,
// lines, highlights}); all three carry `key` = the artifact the step produces.

const commonApi = createApi();
const NO_STEPS = [];
const NO_JOBS = [];
const EMPTY_LOG_STATE = { job: null, log: null, error: null, loading: true, gap: false };

/** Steps and jobs for the whole page, refetched on job start/finish and after a reconnect. */
class JobStore {
  constructor() {
    this.snapshot = { steps: NO_STEPS, jobs: NO_JOBS, loading: true, error: null };
    this.listeners = new Set();
    this.guard = createLatestGuard();
    this.started = false;
  }

  subscribe = (listener) => {
    this.listeners.add(listener);
    if (!this.started) {
      this.started = true;
      this.refresh();
      channel.subscribe(["job_started", "job_finished", "job_progress", "hello"], this.onEvent);
    }
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.snapshot;

  onEvent = (event) => {
    if (event.type === "job_progress") {
      this.patchProgress(event.payload);
    } else if (event.type !== "hello" || event.resync) {
      this.refresh();
    }
  };

  /** Keep a running job's line count and last line current without refetching the list. */
  patchProgress({ id, from, lines }) {
    if (lines.length === 0 || !this.snapshot.jobs.some((job) => job.id === id)) return;
    const jobs = this.snapshot.jobs.map((job) =>
      job.id === id ? { ...job, line_count: from + lines.length, last_line: lines[lines.length - 1] } : job
    );
    this.set({ ...this.snapshot, jobs });
  }

  refresh = async () => {
    const isCurrent = this.guard.begin();
    let next;
    try {
      const [steps, jobs] = await Promise.all([commonApi.get("/steps"), commonApi.get("/jobs")]);
      next = { steps, jobs, loading: false, error: null };
    } catch (error) {
      next = { ...this.snapshot, loading: false, error };
    }
    if (isCurrent()) this.set(next);
  };

  set(snapshot) {
    this.snapshot = snapshot;
    for (const listener of [...this.listeners]) listener();
  }
}

const store = new JobStore();

/**
 * `{steps, jobs, stepsByKey, stepsByArtifact, latestByStep, runningByArtifact, loading, error,
 * refresh}`. Jobs are newest first; a step: {key, title, produces, incremental_safe, paid,
 * command}; a job: {id, step, title, produces, rebuild, state, exit_code, started_at,
 * ended_at, line_count, highlights, cancel_requested}.
 */
export function useJobs() {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return useMemo(() => {
    const steps = indexSteps(snapshot.steps);
    const jobs = indexJobs(snapshot.jobs);
    return {
      ...snapshot,
      stepsByKey: steps.byKey,
      stepsByArtifact: steps.byArtifact,
      latestByStep: jobs.latestByStep,
      runningByArtifact: jobs.runningByArtifact,
      refresh: store.refresh,
    };
  }, [snapshot]);
}

export function startJob(stepKey, { rebuild = false } = {}) {
  return commonApi.post("/jobs", { step: stepKey, rebuild });
}

export function cancelJob(jobId) {
  return commonApi.post(`/jobs/${encodeURIComponent(jobId)}/cancel`);
}

/**
 * One job with its log, following the stream: `{job, log: {start, lines, limit}, error,
 * loading}`. `job` is the server's detail (summary fields), kept current by job events.
 */
export function useJobLog(jobId) {
  const [state, setState] = useState(EMPTY_LOG_STATE);
  const begin = useLatest();
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    if (!jobId) return;
    const isCurrent = begin();
    setState((previous) => ({ ...previous, loading: true }));
    commonApi.get(`/jobs/${encodeURIComponent(jobId)}`).then(
      (job) => {
        if (!isCurrent()) return;
        const log = { start: job.log_start, lines: job.log, limit: job.log_limit };
        setState({ job, log, error: null, loading: false, gap: false });
      },
      (error) => {
        if (isCurrent()) setState({ ...EMPTY_LOG_STATE, error, loading: false });
      }
    );
  }, [jobId, reloads, begin]);

  // Lines were missed (for example while the job's first fetch was in flight): refetch.
  useEffect(() => {
    if (state.gap) setReloads((count) => count + 1);
  }, [state.gap]);

  useEvents(["job_progress", "job_finished", "hello"], (event) => {
    if (event.type === "hello") {
      if (event.resync) setReloads((count) => count + 1);
      return;
    }
    if (event.payload?.id !== jobId) return;
    setState((previous) => {
      if (!previous.job) return previous;
      if (event.type === "job_finished") return { ...previous, job: { ...previous.job, ...event.payload } };
      const log = appendProgress(previous.log, event.payload);
      if (log === null) return { ...previous, gap: true };
      if (log === previous.log) return previous;
      const highlights = [...previous.job.highlights, ...event.payload.highlights];
      return { ...previous, log, job: { ...previous.job, highlights } };
    });
  });

  return state;
}

// Which dialog of the step runner is open: the job panel (with a selected job) or the
// confirmation before a paid run or a rebuild. Rendered once by JobHost.
let ui = { panelOpen: false, panelJobId: null, confirm: null };
const uiListeners = new Set();

function setUi(next) {
  ui = { ...ui, ...next };
  for (const listener of [...uiListeners]) listener();
}

export function useJobUi() {
  return useSyncExternalStore(
    (listener) => {
      uiListeners.add(listener);
      return () => uiListeners.delete(listener);
    },
    () => ui
  );
}

/** Open the job panel, showing `jobId` (default: the newest job). */
export function openJobPanel(jobId = null) {
  setUi({ panelOpen: true, panelJobId: jobId });
}

export function closeJobPanel() {
  setUi({ panelOpen: false });
}

export function selectPanelJob(jobId) {
  setUi({ panelJobId: jobId });
}

/** Ask before running: `{step, rebuild}`; JobHost shows the dialog. */
export function askToRun(request) {
  setUi({ confirm: request });
}

export function closeConfirm() {
  setUi({ confirm: null });
}
