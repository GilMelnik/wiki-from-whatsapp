import { countText, formatCount, useBanner, useJobs, useResource, useShortcuts, useStepRun, useStoredState } from "@shell";
import { api } from "./api.js";
import { MODES } from "./groups.js";
import { GroupsView } from "./GroupsView.jsx";
import { SuggestionsView } from "./SuggestionsView.jsx";

// Claim-group review: step 5 merges only what it is nearly sure about, so the owner finds the
// remaining merges in a ranked suggestion queue (the default view) and inspects, merges,
// splits and moves in the groups view. Everything follows the files on disk: a step-5 re-run
// underneath shows up without a reload. Nothing here ever calls a paid API: when claims have
// no cached vector yet, the step-5 run (the frame's job runner) is offered instead.

const ID = "groups";

function ModeTabs({ mode, onMode }) {
  return (
    <div role="tablist" className="flex gap-1">
      {MODES.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={mode === option.value}
          className={`btn btn-sm ${mode === option.value ? "btn-primary" : ""}`}
          onClick={() => onMode(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function GroupsTool({ tool }) {
  const [mode, setMode] = useStoredState(ID, "mode", "suggestions");
  const [, setSelectedGroup] = useStoredState(ID, "selected", null);
  const summary = useResource(() => api.get("/summary"), [], { watch: tool.reads });
  const run = useStepRun();
  const { stepsByArtifact } = useJobs();
  const step = stepsByArtifact.groups;
  const runStep = step ? () => run(step.key) : null;

  const missing = summary.data?.suggestions.missing_embeddings ?? 0;
  useBanner(
    "groups.embeddings",
    missing > 0
      ? {
          tone: "warning",
          text: `No vector yet for ${countText(missing, "claim", formatCount)} (step 5 has not run on them): there are no merge suggestions, and editing is blocked until step 5 runs.`,
          actionLabel: runStep ? "Run step 5" : undefined,
          onAction: runStep ?? undefined,
        }
      : null
  );

  useShortcuts(
    MODES.map((option) => ({ keys: option.key, label: `Show ${option.label.toLowerCase()}`, run: () => setMode(option.value) }))
  );

  const modeTabs = <ModeTabs mode={mode} onMode={setMode} />;
  const openGroup = (groupId) => {
    setSelectedGroup(groupId);
    setMode("groups");
  };
  return mode === "groups" ? (
    <GroupsView tool={tool} toolId={ID} modeTabs={modeTabs} summary={summary} />
  ) : (
    <SuggestionsView tool={tool} toolId={ID} modeTabs={modeTabs} summary={summary} onOpenGroup={openGroup} runStep={runStep} />
  );
}

export default { id: ID, title: "Claim groups", Component: GroupsTool };
