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
import { artifactState, firstToShow } from "./status.js";

// Pipeline status. Decided here: which step to run next (or by hand), and which tool to open
// to review a file. The list is the pipeline in order with one state per file (running, last
// run failed, out of date, not made yet, up to date) and when it last changed; the main
// column says why and offers the run, with the review tools, the inputs that changed, the
// last run's log, and folded away the files and metadata. Everything follows the files on
// disk live: the shell refetches GET /api/artifacts on every `artifact_changed`, and the
// detail is watched by its artifact key.

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
  const [stored, setSelectedKey] = useStoredState(ID, "selected", null);
  const selectedKey = status.byKey[stored] ? stored : firstToShow(status.artifacts, stateOf);
  const detail = useResource(
    () => (selectedKey ? api.get(`/artifacts/${encodeURIComponent(selectedKey)}`) : null),
    [selectedKey],
    { watch: selectedKey ? [selectedKey] : [] }
  );

  useShortcuts([
    {
      keys: "r",
      label: "Refresh",
      run: () => {
        status.refresh();
        detail.reload();
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
        items={status.artifacts}
        getKey={(artifact) => artifact.key}
        selectedKey={selectedKey}
        onSelect={setSelectedKey}
        renderItem={(artifact) => <ArtifactRow artifact={artifact} state={stateOf(artifact)} now={now} />}
      />
    </PanelState>
  );
  const artifact = status.byKey[selectedKey];
  return (
    <Layout
      toolId={tool.id}
      list={list}
      main={
        <ArtifactDetail
          artifact={artifact}
          {...(artifact ? { state: stateOf(artifact), ...contextOf(artifact) } : {})}
          detail={detail}
          byKey={status.byKey}
          artifacts={status.artifacts}
          now={now}
          onSelect={setSelectedKey}
        />
      }
      listWidth={{ initial: 300 }}
    />
  );
}

export default { id: ID, title: "Status", Component: StatusTool };
