import { useEffect, useRef, useState } from "react";
import { ItemList, Layout, PanelState, useBanner, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { ActionsPanel } from "./ActionsPanel.jsx";
import { api } from "./api.js";
import { DialogHost } from "./Dialogs.jsx";
import { CategoryView, LabelView } from "./ItemViews.jsx";
import { Overview } from "./Overview.jsx";
import { PageView } from "./PageView.jsx";
import { kindOf, labelItems, LABELS_VIEW, nodeById, OVERVIEW_ID, reorderOp, TREE_VIEW, treeItems } from "./plan.js";
import { LabelRow, TreeRow } from "./Rows.jsx";
import { Toolbar } from "./Toolbar.jsx";

// The wiki plan: the page tree the planning model proposed, made the owner's. The list is the
// tree (phases → categories → pages) or the labels by kind; the main column shows the
// selected item (for a page: its scope, with example claims and coverage beside it); the side
// column holds the actions. Every change is one validated plan operation on the server,
// previewed in a dialog first when it changes the structure or a scope; the last changes can
// be undone. A re-plan underneath shows up without a reload.

const ID = "plan";

function PlanTool({ tool }) {
  const toast = useToast();
  const [view, setView] = useStoredState(ID, "view", TREE_VIEW);
  const [nodeId, setNodeId] = useStoredState(ID, "node", OVERVIEW_ID);
  const [labelId, setLabelId] = useStoredState(ID, "label", null);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);
  const scopeRef = useRef(null);

  const plan = useResource(() => api.get("/plan"), [], { watch: tool.reads });
  const overview = plan.data;
  const ready = Boolean(overview?.nodes);
  const labelsView = view === LABELS_VIEW;
  const items = labelsView ? labelItems(overview) : treeItems(overview);
  const selectedId = labelsView ? labelId : nodeId;
  const kind = kindOf(overview, selectedId);

  // Keep the selection on something that exists (after a merge, a delete or a re-plan).
  useEffect(() => {
    if (!ready) return;
    if (nodeId !== OVERVIEW_ID && !nodeById(overview, nodeId)) setNodeId(OVERVIEW_ID);
    if (!overview.labels[labelId]) setLabelId(labelItems(overview)[0]?.id ?? null);
  }, [overview]);

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
          text: `הקובץ ${overview.file} אינו תוכנית תקינה, ולכן כל שינוי יידחה עד שהבעיות יתוקנו: ${overview.errors.join(" · ")}`,
        }
      : null
  );

  const show = (result) => {
    plan.setData(result.overview);
    if (result.focus) select(result.focus);
  };
  /** Save one operation; throws on failure (a dialog shows the error and stays open). */
  const save = async (op) => {
    setSaving(true);
    try {
      const result = await api.post("/operations", op);
      show(result);
      toast.success(result.label);
      return result;
    } finally {
      setSaving(false);
    }
  };
  /** Save from the main column: report a failure as a toast, and still throw it. */
  const quick = (op) =>
    save(op).catch((error) => {
      toast.error(error);
      throw error;
    });
  const undo = async () => {
    if (saving || !overview?.undo) return;
    setSaving(true);
    try {
      const result = await api.post("/undo");
      show(result);
      toast.success(`בוטל: ${result.label}`);
    } catch (error) {
      toast.error(error);
      plan.reload();
    } finally {
      setSaving(false);
    }
  };

  const open = (dialogKind, itemId = null, extra = {}) => setDialog({ kind: dialogKind, itemId, ...extra });
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
  };
  const node = nodeById(overview, selectedId);
  const can = !saving && ready;
  const addNew = () => {
    if (labelsView) open("addLabel", null, { kind: overview.labels[labelId]?.kind ?? "" });
    else if (kind === "page") open("addPage", null, { parentId: node.parent_id });
    else if (kind === "category") open("addPage", null, { parentId: node.id });
    else open("addCategory", null, { parentId: null });
  };
  useShortcuts([
    { keys: "m", label: "מיזוג (דף או תווית)", run: () => can && (kind === "page" || kind === "label") && open("merge", selectedId) },
    { keys: "s", label: "פיצול דף חדש מהדף", run: () => can && kind === "page" && open("split", selectedId) },
    { keys: "t", label: "העברה לקטגוריה אחרת", run: () => can && node && open("move", selectedId) },
    { keys: "alt+arrowup", label: "הזזה למעלה", run: () => can && node && on.reorder(node.id, -1) },
    { keys: "alt+arrowdown", label: "הזזה למטה", run: () => can && node && on.reorder(node.id, 1) },
    { keys: "n", label: "הוספה (דף, תווית או קטגוריה)", run: () => can && addNew() },
    { keys: "delete", label: "מחיקה", run: () => can && kind && !(kind === "category" && node.children.length) && open("delete", selectedId) },
    { keys: "e", label: "עריכת התחום של הדף", run: () => scopeRef.current?.focus() },
    { keys: "u", label: "ביטול השינוי האחרון", run: undo },
    { keys: "l", label: "מעבר בין עץ הדפים לתוויות", run: () => setView(labelsView ? TREE_VIEW : LABELS_VIEW) },
  ]);

  const list = (
    <PanelState
      loading={plan.loading}
      error={plan.error}
      hasData={ready}
      onRetry={plan.reload}
      empty={ready && items.length === 0}
      emptyText={labelsView ? "אין תוויות בתוכנית" : "אין דפים בתוכנית"}
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

  let main = null;
  if (overview?.unreadable) main = <PanelState error={{ message: `הקובץ ${overview.file} אינו בצורה של תוכנית (C7).` }} />;
  else if (!ready) main = <PanelState loading={plan.loading} error={plan.error} onRetry={plan.reload} />;
  else if (!labelsView && selectedId === OVERVIEW_ID) main = <Overview overview={overview} reads={tool.reads} on={on} />;
  else if (kind === "page") main = <PageView overview={overview} pageId={selectedId} saving={saving} reads={tool.reads} scopeRef={scopeRef} on={on} />;
  else if (kind === "category") main = <CategoryView overview={overview} nodeId={selectedId} saving={saving} on={on} />;
  else if (kind === "label") main = <LabelView overview={overview} labelId={selectedId} saving={saving} on={on} />;
  else main = <PanelState empty emptyText="בחרו פריט מהרשימה" />;

  return (
    <>
      <Layout
        toolId={tool.id}
        toolbar={<Toolbar overview={overview} view={view} onView={setView} onSelect={select} onUndo={undo} saving={saving} />}
        list={list}
        main={main}
        side={ready && <ActionsPanel overview={overview} selectedId={selectedId} labelsView={labelsView} saving={saving} on={on} />}
        listWidth={{ initial: 340 }}
      />
      <DialogHost dialog={dialog} overview={overview} close={() => setDialog(null)} save={save} />
    </>
  );
}

export default { id: ID, title: "תוכנית הוויקי", Component: PlanTool };
