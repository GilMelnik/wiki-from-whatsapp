import { useEffect, useMemo, useRef, useState } from "react";
import { ItemList, kindOf, labelItems, Layout, nodeById, PanelState, useBanner, useJobs, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { ActionsPanel } from "./ActionsPanel.jsx";
import { api } from "./api.js";
import {
  assignmentRows,
  DETAILS_TAB,
  GROUPS_TAB,
  isAssignmentRow,
  isProposalRow,
  nextProposalRow,
  proposalOfRow,
  proposalRowId,
  PROPOSALS_ID,
  UNPLACED_ID,
  WAITING_ID,
} from "./assign.js";
import { GroupsPanel } from "./AssignmentViews.jsx";
import { DialogHost } from "./Dialogs.jsx";
import { CategoryView, LabelView } from "./ItemViews.jsx";
import { Overview } from "./Overview.jsx";
import { PageView } from "./PageView.jsx";
import { LABELS_VIEW, OVERVIEW_ID, reorderOp, TOOL_ID, TREE_VIEW, treeItems } from "./plan.js";
import { ProposalDetail, ProposalsView } from "./ProposalsView.jsx";
import { LabelRow, TreeRow } from "./Rows.jsx";
import { Toolbar } from "./Toolbar.jsx";

// The wiki plan: the page tree the planning model proposed, made the owner's, and (once step
// 6b has run) where the classifier put every claim group. The list is the tree (phases →
// categories → pages, with the groups on no page and the step's proposals, each to decide,
// on top) or the labels by kind; the main column shows the selected item (the overview: what
// there is to do; a page: its scope next to a sample of its groups, the closest groups it did
// not get and its exact size, or all its claim groups; a proposal: its groups and dry run);
// the side column holds the actions, or the decision on a proposal.
// Every plan change is one validated operation on the server, previewed in a dialog first
// when it changes the structure or a scope; group moves pin the groups; the last changes of
// both kinds can be undone. A re-plan or an assignment run underneath shows up without a
// reload.

const ID = TOOL_ID;

function PlanTool({ tool }) {
  const toast = useToast();
  const [view, setView] = useStoredState(ID, "view", TREE_VIEW);
  const [nodeId, setNodeId] = useStoredState(ID, "node", OVERVIEW_ID);
  const [labelId, setLabelId] = useStoredState(ID, "label", null);
  const [tab, setTab] = useStoredState(ID, "itemTab", DETAILS_TAB);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const [offer, setOffer] = useState(null);
  const scopeRef = useRef(null);
  const { runningByArtifact } = useJobs();
  // The assignments are a write of the tool (an optional file): views follow them too.
  const watch = useMemo(() => [...new Set([...tool.reads, ...tool.writes])], [tool.reads, tool.writes]);
  // While a step rewrites a file the tool writes, the server refuses saves (423).
  const lockedBy = tool.writes.map((key) => runningByArtifact[key]).find(Boolean) ?? null;
  const locked = Boolean(lockedBy);

  const plan = useResource(() => api.get("/plan"), [], { watch });
  const overview = plan.data;
  const ready = Boolean(overview?.nodes);
  const assigned = Boolean(overview?.assignments?.exists && !overview.assignments.problem);
  const proposals = useResource(() => (assigned ? api.get("/proposals") : null), [assigned], { watch });
  const proposalItems = proposals.data?.items ?? [];
  const labelsView = view === LABELS_VIEW;
  const items = labelsView ? labelItems(overview) : ready ? [treeItems(overview)[0], ...assignmentRows(overview, proposalItems), ...overview.nodes] : [];
  const selectedId = labelsView ? labelId : nodeId;
  const kind = kindOf(overview, selectedId);
  const proposal = labelsView ? null : proposalOfRow(proposalItems, selectedId);

  // Keep the selection on something that exists (after a merge, a delete or a re-plan).
  useEffect(() => {
    if (!ready) return;
    if (nodeId !== OVERVIEW_ID && !isAssignmentRow(nodeId) && !nodeById(overview, nodeId)) setNodeId(OVERVIEW_ID);
    // A proposal decided elsewhere (or gone with a new run) leaves its row: back to the list.
    if (proposals.data && isProposalRow(nodeId) && !proposalOfRow(proposalItems, nodeId)) {
      setNodeId(PROPOSALS_ID);
    }
    if (!overview.labels[labelId]) setLabelId(labelItems(overview)[0]?.id ?? null);
  }, [overview, proposals.data]);

  const select = (id) => {
    if (id && overview.labels[id]) {
      setView(LABELS_VIEW);
      setLabelId(id);
    } else {
      setView(TREE_VIEW);
      setNodeId(id ?? OVERVIEW_ID);
    }
  };

  useBanner(
    "plan.invalid",
    overview?.errors?.length
      ? {
          tone: "danger",
          text: `The file ${overview.file} is not a valid plan, so every change will be refused until the problems are fixed: ${overview.errors.join(" · ")}`,
        }
      : null
  );
  useBanner(
    "plan.assignments-unreadable",
    overview?.assignments?.problem ? { tone: "danger", text: `${overview.assignments.problem} The assignment views are unavailable until the file is fixed or written again.` } : null
  );

  const show = (result) => {
    plan.setData(result.overview);
    if (result.focus) select(result.focus);
  };
  /** Run one save; throws on failure (a dialog shows the error and stays open). */
  const saveWith = async (send) => {
    setSaving(true);
    try {
      return await send();
    } finally {
      setSaving(false);
    }
  };
  const save = (op) =>
    saveWith(async () => {
      const result = await api.post("/operations", op);
      show(result);
      toast.success(result.label);
      return result;
    });
  /** Save from the main column: report a failure as a toast, and still throw it. */
  const quick = (op) =>
    save(op).catch((error) => {
      toast.error(error);
      throw error;
    });
  const editGroups = (groupIds, changes) =>
    saveWith(async () => {
      const result = await api.post("/assignments/edit", { group_ids: groupIds, changes });
      plan.setData(result.overview);
      if (result.changed > 0) toast.success(result.label);
      else toast.info("Nothing to change: the groups are already like that.");
      return result;
    });
  // After a decision on the shown proposal, the next one to decide is shown (else the list).
  const moveOn = (decided) => {
    if (nodeId === proposalRowId(decided.id)) setNodeId(nextProposalRow(proposalItems, decided.id));
  };
  const accept = (decided, op) =>
    saveWith(async () => {
      const result = await api.post(`/proposals/${encodeURIComponent(decided.id)}/accept`, { operation: op });
      plan.setData(result.overview);
      proposals.reload();
      toast.success(result.label);
      setOffer({ label: result.label, focus: result.focus, at: new Date().toISOString() });
      moveOn(decided);
      return result;
    });
  const decide = (decided, action) =>
    saveWith(async () => {
      const result = await api.post(`/proposals/${encodeURIComponent(decided.id)}/${action}`);
      plan.setData(result.overview);
      proposals.reload();
      toast.success(result.label);
      if (action === "reject") moveOn(decided);
    }).catch((error) => toast.error(error));
  const undo = async () => {
    if (saving || !overview?.undo) return;
    setSaving(true);
    try {
      const result = await api.post("/undo");
      show(result);
      toast.success(`Undone: ${result.label}`);
    } catch (error) {
      toast.error(error);
      plan.reload();
    } finally {
      setSaving(false);
    }
  };

  const open = (type, itemId = null, extra = {}) => setDialog({ ...extra, type, itemId });
  const on = {
    select,
    open,
    undo,
    rename: (id, title) => quick({ op: "rename", item_id: id, title_he: title }),
    edit: (id, changes) => quick({ op: "edit", item_id: id, changes }),
    editScope: (id, scope) => open("scope", id, { scope }),
    reorder: (id, step) => {
      const op = reorderOp(nodeById(overview, id), step);
      if (op) quick(op).catch(() => {});
    },
    showTab: setTab,
    openAccept: (chosen) => open("accept", null, { proposal: chosen }),
    decide,
    dismissOffer: () => setOffer(null),
  };
  const node = nodeById(overview, selectedId);
  const can = !saving && ready && !locked;
  const addNew = () => {
    if (labelsView) open("addLabel", null, { kind: overview.labels[labelId]?.kind ?? "" });
    else if (kind === "page") open("addPage", null, { parentId: node.parent_id });
    else if (kind === "category") open("addPage", null, { parentId: node.id });
    else open("addCategory", null, { parentId: null });
  };
  useShortcuts([
    { keys: "m", label: "Merge (a page or a label)", run: () => can && (kind === "page" || kind === "label") && open("merge", selectedId) },
    { keys: "s", label: "Split a new page off the page", run: () => can && kind === "page" && open("split", selectedId) },
    { keys: "t", label: "Move to another category", run: () => can && node && open("move", selectedId) },
    { keys: "alt+arrowup", label: "Move up", run: () => can && node && on.reorder(node.id, -1) },
    { keys: "alt+arrowdown", label: "Move down", run: () => can && node && on.reorder(node.id, 1) },
    { keys: "n", label: "Add (a page, label or category)", run: () => can && addNew() },
    { keys: "delete", label: "Delete", run: () => can && kind && !(kind === "category" && node.children.length) && open("delete", selectedId) },
    { keys: "e", label: "Edit the page's scope", run: () => scopeRef.current?.focus() },
    { keys: "u", label: "Undo the last change", run: undo },
    { keys: "l", label: "Switch between the page tree and the labels", run: () => setView(labelsView ? TREE_VIEW : LABELS_VIEW) },
    {
      keys: "g",
      label: "Switch between the page / label details and its claim groups",
      run: () => (kind === "page" || kind === "label") && setTab(tab === GROUPS_TAB ? DETAILS_TAB : GROUPS_TAB),
    },
  ]);
  // The decision on the shown proposal (bound only then: a and x pick and remove in a group list).
  useShortcuts(
    [
      { keys: "a", label: "Accept the proposal (opens it to correct first)", run: () => can && proposal && !(proposal.kind === "scope" && !proposal.target.page_id) && on.openAccept(proposal) },
      { keys: "x", label: "Reject the proposal", run: () => can && proposal && decide(proposal, "reject") },
    ],
    { enabled: Boolean(proposal) }
  );

  const list = (
    <PanelState
      loading={plan.loading}
      error={plan.error}
      hasData={ready}
      onRetry={plan.reload}
      empty={ready && items.length === 0}
      emptyText={labelsView ? "No labels in the plan" : "No pages in the plan"}
    >
      <ItemList
        items={items}
        getKey={(item) => item.id}
        selectedKey={selectedId}
        onSelect={labelsView ? setLabelId : setNodeId}
        renderItem={(item) => (labelsView ? <LabelRow overview={overview} item={item} /> : <TreeRow overview={overview} item={item} />)}
      />
    </PanelState>
  );

  const groups = { overview, watch, locked, saving, editGroups };
  let main = null;
  if (overview?.unreadable) main = <PanelState error={{ message: `The file ${overview.file} does not have the shape of a plan (C7).` }} />;
  else if (!ready) main = <PanelState loading={plan.loading} error={plan.error} onRetry={plan.reload} />;
  else if (!labelsView && selectedId === OVERVIEW_ID) main = <Overview overview={overview} reads={watch} on={on} />;
  else if (!labelsView && (selectedId === UNPLACED_ID || selectedId === WAITING_ID)) {
    main = <GroupsPanel {...groups} scope={selectedId === UNPLACED_ID ? "unplaced" : "waiting"} />;
  } else if (!labelsView && selectedId === PROPOSALS_ID) {
    main = <ProposalsView overview={overview} proposals={proposals} busy={saving || locked} offer={offer} on={on} />;
  } else if (proposal) {
    main = <ProposalDetail proposal={proposal} overview={overview} offer={offer} on={on} />;
  } else if (kind === "page") {
    main = <PageView overview={overview} pageId={selectedId} saving={saving} reads={watch} scopeRef={scopeRef} tab={tab} groups={groups} on={on} />;
  } else if (kind === "category") main = <CategoryView overview={overview} nodeId={selectedId} saving={saving} on={on} />;
  else if (kind === "label") main = <LabelView overview={overview} labelId={selectedId} saving={saving} tab={tab} groups={groups} on={on} />;
  else main = <PanelState empty emptyText="Select an item in the list" />;

  return (
    <>
      <Layout
        toolId={tool.id}
        toolbar={<Toolbar overview={overview} view={view} onView={setView} onUndo={undo} saving={saving || locked} />}
        list={list}
        main={main}
        side={
          ready && (
            <ActionsPanel
              overview={overview}
              selectedId={selectedId}
              proposal={proposal}
              labelsView={labelsView}
              saving={saving || locked}
              lockedBy={lockedBy}
              tab={tab}
              on={on}
            />
          )
        }
        listWidth={{ initial: 340 }}
      />
      <DialogHost dialog={dialog} overview={overview} close={() => setDialog(null)} save={save} accept={accept} />
    </>
  );
}

export default { id: ID, title: "Wiki plan", Component: PlanTool };
