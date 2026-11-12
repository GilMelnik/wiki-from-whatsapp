import { useEffect, useMemo, useRef } from "react";
import {
  createApi,
  ItemList,
  Layout,
  PanelState,
  useArtifactStatus,
  useJobs,
  useNow,
  useResource,
  useShortcuts,
  useStoredState,
} from "@shell";
import { ArtifactDetail } from "./ArtifactDetail.jsx";
import { ArtifactRow } from "./ArtifactRow.jsx";
import { artifactState, firstToShow, showsUpdate, UPDATE_KEY } from "./status.js";
import { UpdateDetail, UpdateRow } from "./UpdateDetail.jsx";

// Pipeline status. Decided here: which step to run next (or by hand), and which tool to open
// to review a file; during an update, what the update waits for. The list is the pipeline in
// order with one state per file (running, last run failed, out of date, not made yet, up to
// date) and when it last changed, under an "Update in progress" row while an update
// (step_10_update) is not completed: that row is shown first, with the driver's stop, the
// estimate before a paid step, the command to go on and the steps as a timeline. For a file
// the main column says why and offers the run, with the review tools, the inputs that
// changed, the last run's log, and folded away the files and metadata. Everything follows
// the files on disk live: the shell refetches GET /api/artifacts on every
// `artifact_changed`, the detail is watched by its artifact key, and the update view by
// every key (a step of the update writes one) and on the clock.

const ID = "status";
const api = createApi(ID);

function StatusTool({ tool }) {
  const status = useArtifactStatus();
  const { stepsByArtifact, runningByArtifact, latestByStep } = useJobs();
  const now = useNow();
  const contextOf = (artifact) => {
    const step = stepsByArtifact[artifact.key];
    return { step, running: runningByArtifact[artifact.key], latest: step ? latestByStep[step.key] : undefined };
  };
  const stateOf = (artifact) => artifactState(artifact, contextOf(artifact));
  const allKeys = useMemo(() => status.artifacts.map((artifact) => artifact.key), [status.artifacts]);
  const update = useResource(() => api.get("/update"), [], { watch: allKeys });
  const inProgress = showsUpdate(update.data);
  useEffect(() => {
    if (inProgress) update.reload(); // the driver also writes between steps: follow it on the clock
  }, [now]);
  const [stored, setSelectedKey] = useStoredState(ID, "selected", null);
  // An update in progress is what the owner sees first when the tool opens.
  const updateShown = useRef(false);
  useEffect(() => {
    if (inProgress && !updateShown.current) setSelectedKey(UPDATE_KEY);
    if (update.data) updateShown.current = true;
  }, [update.data]);
  const selectedKey =
    (stored === UPDATE_KEY && inProgress) || status.byKey[stored] ? stored : firstToShow(status.artifacts, stateOf);
  const artifactKey = selectedKey === UPDATE_KEY ? null : selectedKey;
  const detail = useResource(
    () => (artifactKey ? api.get(`/artifacts/${encodeURIComponent(artifactKey)}`) : null),
    [artifactKey],
    { watch: artifactKey ? [artifactKey] : [] }
  );

  useShortcuts([
    {
      keys: "r",
      label: "Refresh",
      run: () => {
        status.refresh();
        detail.reload();
        update.reload();
      },
    },
  ]);

  const list = (
    <PanelState
      loading={status.loading}
      error={status.error}
      hasData={status.artifacts.length > 0}
      onRetry={status.refresh}
      empty={status.artifacts.length === 0}
      emptyText="The server lists no files"
    >
      <ItemList
        items={inProgress ? [{ key: UPDATE_KEY }, ...status.artifacts] : status.artifacts}
        getKey={(row) => row.key}
        selectedKey={selectedKey}
        onSelect={setSelectedKey}
        renderItem={(row) =>
          row.key === UPDATE_KEY ? <UpdateRow update={update.data} /> : <ArtifactRow artifact={row} state={stateOf(row)} now={now} />
        }
      />
    </PanelState>
  );
  const artifact = status.byKey[selectedKey];
  const main =
    selectedKey === UPDATE_KEY ? (
      <UpdateDetail update={update.data} />
    ) : (
      <ArtifactDetail
        artifact={artifact}
        {...(artifact ? { state: stateOf(artifact), ...contextOf(artifact) } : {})}
        detail={detail}
        byKey={status.byKey}
        artifacts={status.artifacts}
        now={now}
        onSelect={setSelectedKey}
      />
    );
  return <Layout toolId={tool.id} list={list} main={main} listWidth={{ initial: 300 }} />;
}

export default { id: ID, title: "Status", Component: StatusTool };
