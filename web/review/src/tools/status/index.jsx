import {
  createApi,
  ItemList,
  Layout,
  PanelState,
  useArtifactStatus,
  useEvents,
  useNow,
  useResource,
  useShortcuts,
  useStoredState,
  useToast,
} from "@shell";
import { ArtifactDetail } from "./ArtifactDetail.jsx";
import { ArtifactRow } from "./ArtifactRow.jsx";
import { StepPanel } from "./StepPanel.jsx";
import { ToolsPanel } from "./ToolsPanel.jsx";

// Pipeline status: every artifact in pipeline order (list), the selected one's files,
// staleness and metadata (main), the step that produces it with its run buttons and last
// run, and the tools that read or write it (side). Everything follows the files on disk
// live: the shell refetches GET /api/artifacts on every `artifact_changed`, and the detail
// is watched by its artifact key.

const ID = "status";
const api = createApi(ID);

function StatusTool({ tool }) {
  const status = useArtifactStatus();
  const toast = useToast();
  const now = useNow();
  const [stored, setSelectedKey] = useStoredState(ID, "selected", null);
  const selectedKey = status.byKey[stored] ? stored : status.artifacts[0]?.key ?? null;
  const detail = useResource(
    () => (selectedKey ? api.get(`/artifacts/${encodeURIComponent(selectedKey)}`) : null),
    [selectedKey],
    { watch: selectedKey ? [selectedKey] : [] }
  );

  useEvents("artifact_changed", (event) => {
    toast.info(`${status.byKey[event.key]?.title ?? event.key}: הקובץ השתנה`);
  });
  useShortcuts([
    {
      keys: "r",
      label: "רענון",
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
    >
      <ItemList
        items={status.artifacts}
        getKey={(artifact) => artifact.key}
        selectedKey={selectedKey}
        onSelect={setSelectedKey}
        renderItem={(artifact) => <ArtifactRow artifact={artifact} now={now} />}
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
          detail={detail}
          byKey={status.byKey}
          artifacts={status.artifacts}
          now={now}
          onSelect={setSelectedKey}
        />
      }
      side={
        <div className="flex min-h-0 flex-1 flex-col divide-y divide-slate-200">
          <div className="flex min-h-0 flex-[2] flex-col">
            <StepPanel artifact={artifact} />
          </div>
          <div className="flex min-h-0 flex-1 flex-col">
            <ToolsPanel artifact={artifact} />
          </div>
        </div>
      }
      listWidth={{ initial: 320 }}
      sideWidth={{ initial: 300 }}
    />
  );
}

export default { id: ID, title: "מצב הצינור", Component: StatusTool };
