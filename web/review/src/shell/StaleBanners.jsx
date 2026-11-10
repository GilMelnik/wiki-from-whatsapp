import { useBanner } from "./Banner.jsx";
import { artifactNotice, LOG_ACTION, RUN_ACTION } from "./jobState.js";
import { openJobPanel, useJobs } from "./jobs.js";
import { useArtifactStatus } from "./serverState.js";
import { useStepRun } from "./JobControls.jsx";

// Automatic notices for every tool, from its `reads` and `writes` alone (no tool code):
// - an artifact it reads is older than one of its inputs → which input changed and when, and
//   the button that re-runs the producing step (or "run it by hand" when the step is not
//   incremental-safe yet);
// - a step is rewriting an artifact it reads or writes → "running", saves blocked (the server
//   answers 423), log one click away;
// - a step is rewriting one of its inputs → wait for it (no run button yet);
// - the last run since the change failed → the banner stays, with the log.

function ArtifactBanner({ artifactKey, writes }) {
  const { byKey } = useArtifactStatus();
  const { stepsByArtifact, latestByStep, runningByArtifact } = useJobs();
  const run = useStepRun();
  const step = stepsByArtifact[artifactKey];
  const artifact = byKey[artifactKey];
  const notice = artifactNotice({
    artifact,
    byKey,
    step,
    running: runningByArtifact[artifactKey],
    feeding: artifact?.upstream.map((key) => runningByArtifact[key]).find(Boolean),
    latest: step ? latestByStep[step.key] : undefined,
    writes,
  });
  const onAction = () => {
    if (notice?.action === RUN_ACTION) run(step.key);
    else if (notice?.action === LOG_ACTION) openJobPanel(notice.jobId);
  };
  useBanner(
    `shell.artifact.${artifactKey}`,
    notice && { tone: notice.tone, text: notice.text, actionLabel: notice.actionLabel, onAction }
  );
  return null;
}

/** One notice slot per artifact the tool reads or writes (rendered by the shell). */
export function StaleBanners({ tool }) {
  if (!tool) return null;
  const keys = [...new Set([...tool.reads, ...tool.writes])];
  return keys.map((key) => (
    <ArtifactBanner key={key} artifactKey={key} writes={tool.writes.includes(key)} />
  ));
}
