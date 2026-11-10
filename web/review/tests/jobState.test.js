import assert from "node:assert/strict";
import { test } from "node:test";
import {
  appendProgress,
  artifactNotice,
  FAILED,
  indexJobs,
  LOG_ACTION,
  RUN_ACTION,
  RUNNING,
  SUCCEEDED,
} from "../src/shell/jobState.js";

const threads = { key: "threads", title: "שיחות", mtime: "2026-11-10T10:00:00.000+00:00", edited_mtime: null, stale_because: [] };
const classified = { key: "classified", title: "סיווג שיחות", mtime: "2026-11-10T09:00:00.000+00:00", stale_because: ["threads"] };
const byKey = { threads, classified };
const safeStep = { key: "step_2", title: "שלב 2", produces: "classified", incremental_safe: true, paid: false, command: "python -m step_2" };

test("a stale artifact offers the run button of an incremental-safe step", () => {
  const notice = artifactNotice({ artifact: classified, byKey, step: safeStep });
  assert.equal(notice.tone, "warning");
  assert.equal(notice.action, RUN_ACTION);
  assert.equal(notice.actionLabel, "הרצת השלב");
  assert.match(notice.text, /"שיחות" \(.+\) השתנה/);
  assert.match(notice.text, /שלב 2/);
});

test("a paid step's button says a dialog follows", () => {
  const notice = artifactNotice({ artifact: classified, byKey, step: { ...safeStep, paid: true } });
  assert.equal(notice.actionLabel, "הרצת השלב…");
});

test("a step that is not incremental-safe has no button, only how to run it by hand", () => {
  const notice = artifactNotice({ artifact: classified, byKey, step: { ...safeStep, incremental_safe: false } });
  assert.equal(notice.action, null);
  assert.equal(notice.actionLabel, null);
  assert.match(notice.text, /python -m step_2/);
});

test("nothing to say about an up-to-date artifact or one no step produces", () => {
  assert.equal(artifactNotice({ artifact: threads, byKey, step: safeStep }), null);
  assert.equal(artifactNotice({ artifact: classified, byKey, step: undefined }), null);
  assert.equal(artifactNotice({ artifact: undefined, byKey, step: safeStep }), null);
});

test("a running job replaces the notice; writers are told saves are blocked", () => {
  const running = { id: "j1", title: "שלב 2", state: RUNNING };
  const reader = artifactNotice({ artifact: classified, byKey, step: safeStep, running, writes: false });
  const writer = artifactNotice({ artifact: { ...classified, stale_because: [] }, byKey, step: safeStep, running, writes: true });
  assert.equal(reader.action, LOG_ACTION);
  assert.equal(reader.jobId, "j1");
  assert.ok(!reader.text.includes("חסומה"));
  assert.match(writer.text, /חסומה/);
});

test("while the step that feeds it runs, the notice waits instead of offering a run", () => {
  const feeding = { id: "j0", title: "שלב 1", produces: "threads", state: RUNNING };
  const notice = artifactNotice({ artifact: classified, byKey, step: safeStep, feeding });
  assert.equal(notice.action, LOG_ACTION);
  assert.equal(notice.jobId, "j0");
  assert.match(notice.text, /"שלב 1" רץ כעת ומעדכן את "שיחות"/);
});

test("a run that failed after the change keeps a danger notice with the log", () => {
  const latest = { id: "j2", state: FAILED, exit_code: 1, started_at: "2026-11-10T10:05:00.000+00:00" };
  const notice = artifactNotice({ artifact: classified, byKey, step: safeStep, latest });
  assert.equal(notice.tone, "danger");
  assert.equal(notice.action, LOG_ACTION);
  assert.equal(notice.jobId, "j2");
  const before = { ...latest, started_at: "2026-11-10T09:30:00.000+00:00" };
  assert.equal(artifactNotice({ artifact: classified, byKey, step: safeStep, latest: before }).action, RUN_ACTION);
});

test("jobs index: newest job per step, running job per artifact", () => {
  const jobs = [
    { id: "new", step: "s2", produces: "classified", state: RUNNING },
    { id: "old", step: "s2", produces: "classified", state: SUCCEEDED },
    { id: "other", step: "s3", produces: "claims", state: FAILED },
  ];
  const { latestByStep, runningByArtifact } = indexJobs(jobs);
  assert.equal(latestByStep.s2.id, "new");
  assert.equal(latestByStep.s3.id, "other");
  assert.deepEqual(Object.keys(runningByArtifact), ["classified"]);
});

test("progress lines append, overlap is skipped, a gap asks for a refetch, the limit holds", () => {
  const log = { start: 0, lines: ["a", "b"], limit: 3 };
  assert.deepEqual(appendProgress(log, { from: 2, lines: ["c"] }).lines, ["a", "b", "c"]);
  assert.deepEqual(appendProgress(log, { from: 1, lines: ["b", "c"] }).lines, ["a", "b", "c"]);
  assert.equal(appendProgress(log, { from: 0, lines: ["a"] }), log);
  assert.equal(appendProgress(log, { from: 3, lines: ["d"] }), null);
  const trimmed = appendProgress(log, { from: 2, lines: ["c", "d"] });
  assert.deepEqual(trimmed, { start: 1, lines: ["b", "c", "d"], limit: 3 });
});
