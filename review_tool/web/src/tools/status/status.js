import { countText, FAILED, formatCount } from "@shell";

// What one artifact's row and detail say about it (pure, tested in node): the one state the
// owner acts on, most urgent first. `running` is the job rewriting it, `latest` the newest
// run of its step.

const ATTENTION = ["running", "failed", "stale"];

/** {kind, label, tone} — kind: running | failed | stale | missing | input | current. */
export function artifactState(artifact, { step, running, latest }) {
  const hasFile = artifact.exists || artifact.edited_exists;
  if (running) return { kind: "running", label: "Running", tone: "sky" };
  if (!hasFile) return { kind: "missing", label: step ? "Not made yet" : "No file", tone: "slate" };
  if (artifact.stale_because.length > 0) {
    if (latest?.state === FAILED) return { kind: "failed", label: "Last run failed", tone: "rose" };
    return { kind: "stale", label: "Out of date", tone: "amber" };
  }
  if (!step) return { kind: "input", label: "Input", tone: "slate" };
  return { kind: "current", label: "Up to date", tone: "emerald" };
}

/** The artifact to show first: the earliest one that needs attention, else the first. */
export function firstToShow(artifacts, stateOf) {
  return (artifacts.find((artifact) => ATTENTION.includes(stateOf(artifact).kind)) ?? artifacts[0])?.key ?? null;
}

// ---------------------------------------------------------------- an update in progress

/** The list key of the update row (artifact keys never start with an underscore). */
export const UPDATE_KEY = "_update";

// A step of the update's timeline: label and tone (the row dots' tones).
export const UPDATE_STEP_STATES = {
  done: { label: "Done", tone: "emerald" },
  review: { label: "Done · waiting for your review", tone: "amber" },
  running: { label: "Running", tone: "sky" },
  failed: { label: "Failed", tone: "rose" },
  stopped: { label: "Stopped before it", tone: "amber" },
  next: { label: "Next", tone: "slate" },
  waiting: { label: "Later", tone: "slate" },
};

/** Whether the status tool shows the update (first): one not completed, or a state file it cannot read. */
export function showsUpdate(update) {
  return update?.state === "in_progress" || update?.state === "unreadable";
}

/** {label, tone} of the update as a whole, for its list row. */
export function updateState(update) {
  if (update.state === "unreadable") return { label: "State file unreadable", tone: "rose" };
  if (update.running) return { label: `Running ${update.running.title ?? update.running.step}`, tone: "sky" };
  if (update.failed) return { label: "A step failed", tone: "rose" };
  const kind = update.stopped?.kind;
  if (kind === "review") return { label: "Waiting for your review", tone: "amber" };
  if (kind === "paid") return { label: "Waiting for your go-ahead to pay", tone: "amber" };
  if (kind === "busy") return { label: "Waited for the review server", tone: "amber" };
  return { label: "Paused", tone: "slate" };
}

/** "120 new messages, 3 changed, 2 missing from it (kept)" from the update's counts. */
export function exportText(counts) {
  const parts = [countText(counts.new ?? 0, "new message", formatCount, "new messages"), `${formatCount(counts.changed ?? 0)} changed`];
  if (counts.missing) parts.push(`${formatCount(counts.missing)} missing from it (kept)`);
  return parts.join(", ");
}

/** The estimate recorded before a paid step, in one line. */
export function estimateText(estimate) {
  const calls = countText(estimate.paid_calls, "paid call");
  return `${estimate.what}: ${calls}, about $${estimate.usd.toFixed(2)} (rough: ${estimate.counted})`;
}
