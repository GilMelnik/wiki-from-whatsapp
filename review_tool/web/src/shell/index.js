// Everything a tool may use, imported as `import { ... } from "@shell"`. Tools never deep-import
// shell files and never import from another tool (`npm run check` enforces both).

// The page (used by main.jsx only)
export { App } from "./App.jsx";

// Talking to the server
export { ApiError, createApi } from "./api.js";
export { useArtifactChange, useConnection, useEvents } from "./events.js";
export { useArtifactStatus, useTools } from "./serverState.js";
export { useLatest } from "./latest.js";
export { useResource } from "./useResource.js";
export { useJobs } from "./jobs.js";
export { JOB_STATE_LABELS } from "./jobState.js";

// Frame and building blocks
export { Layout } from "./Layout.jsx";
export { Panel } from "./Panel.jsx";
export { ItemList } from "./ItemList.jsx";
export { Pager } from "./Pager.jsx";
export { pageStart, usePagedSelection } from "./pagedSelection.js";
export { pickState, setPicked, togglePicked, usePicking } from "./picking.js";
export { Select } from "./Select.jsx";
export { PanelState } from "./states.jsx";
export { Dialog } from "./Dialog.jsx";
export { useToast } from "./Toast.jsx";
export { useBanner } from "./Banner.jsx";
export { ToolLink } from "./navigation.jsx";
export { JobLog, useStepRun } from "./JobControls.jsx";
export { ActionButton } from "./ActionButton.jsx";

// Plan operations, shared by every tool that shows or edits one (plan, suggestions): the pure
// helpers (also what a tool's pure modules may import, see pure.js), the forms and dialogs,
// and the plan overview they are built on.
export * from "./pure.js";
export { usePlanOverview, usePreview } from "./planApi.js";
export { EditableText, IdField, submitOnEnter, TextArea } from "./fields.jsx";
export { Consequences, OperationDialog } from "./OperationDialog.jsx";
export { DryRun } from "./DryRun.jsx";
export { AddCategoryDialog, AddLabelDialog, AddPageDialog, KindField, MergeDialog, SplitDialog } from "./PlanDialogs.jsx";

// The shared message views (conversation and timeline), for every tool that shows messages;
// their pure helpers (rows, gaps, edges, selection rules, colours) come with pure.js.
export { ConversationView, ROLE_LABELS } from "./ConversationView.jsx";
export { TimelineView } from "./TimelineView.jsx";
export { Divider, MessageRow, ThreadTag } from "./MessageRow.jsx";
export { MESSAGE_VIEWS, MessageViewBar } from "./MessageViewBar.jsx";
export { useMessageSelection, useThreadColors } from "./messageHooks.js";

// Text, formatting, keyboard, remembered state
export { Bidi, Kbd, Ltr } from "./text.jsx";
export { useNow } from "./useNow.js";
export { useShortcuts } from "./useShortcuts.js";
export { useStoredState } from "./storage.js";
