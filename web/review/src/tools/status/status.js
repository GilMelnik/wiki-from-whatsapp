import { FAILED } from "@shell";

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
