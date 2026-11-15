import { useEffect, useState } from "react";
import { createApi, ItemList, Layout, PanelState, Select, useResource, useShortcuts, useStoredState, useToast } from "@shell";
import { DecisionPanel, NAME_FIELD_ID } from "./DecisionPanel.jsx";
import { MemberDetail } from "./MemberDetail.jsx";
import { MemberRow } from "./MemberRow.jsx";
import { decisionToast, DEFAULT_SHOW, emptyListText, nextSelection, showOptions, TABLE_KEY, TOOL_ID } from "./tagged.js";

// Tagged members: some providers are members of the group, and members recommend them by
// tagging them, which the chat writes as "@<their number>". Decided here, once per tagged
// member: a provider, under which public name step 3 shows their tag, or not a provider (shown
// as an anonymous member, like an undecided one). The list holds the tagged members, the most
// tagged first (by default the undecided ones that several members tag); the main column what
// the decision rests on: the suggested names from step 3's claims, the conversations (links to
// the Conversations page) and the tagging messages; the side column the decision. It comes
// before step 3: a change makes step 3 read the conversations whose tags show differently.

const api = createApi(TOOL_ID);
const memberPath = (member) => `/members/${encodeURIComponent(member)}`;

function Toolbar({ show, onShow, summary }) {
  const counts = summary?.counts;
  return (
    <div className="flex flex-wrap items-center gap-4 text-sm">
      <Select label="Show" value={show} options={showOptions(summary?.min_taggers)} onChange={onShow} />
      {counts && (
        <span className="text-xs text-slate-500">
          {counts.waiting} waiting · {counts.provider} providers · {counts.not_provider} not providers · {counts.undecided} undecided of{" "}
          {counts.all} tagged members
        </span>
      )}
    </div>
  );
}

function TaggedTool({ tool }) {
  const toast = useToast();
  const [show, setShow] = useStoredState(TOOL_ID, "show", DEFAULT_SHOW);
  const [selectedId, setSelectedId] = useStoredState(TOOL_ID, "selected", null);
  const [draft, setDraft] = useState({ member: null, name: "" });
  const [saving, setSaving] = useState(false);
  const watch = [...tool.reads, TABLE_KEY];

  const summary = useResource(() => api.get("/summary"), [], { watch });
  const list = useResource(() => api.get("/members", { show }), [show], { watch, keepPrevious: true });
  const detail = useResource(() => (selectedId ? api.get(memberPath(selectedId)) : null), [selectedId], { watch });

  const items = list.data?.items ?? [];
  const member = detail.data?.member === selectedId ? detail.data : null;
  const name = draft.member === selectedId ? draft.name : member?.public_name ?? "";
  useEffect(() => {
    if (!selectedId && items.length > 0) setSelectedId(items[0].member);
  }, [selectedId, items]);

  const save = async (path, body) => {
    if (saving || !member) return;
    setSaving(true);
    const before = items;
    try {
      const answer = await api.post(path, body);
      detail.setData(answer.member);
      summary.setData(answer.summary);
      const after = await api.get("/members", { show });
      list.setData(after);
      setDraft({ member: null, name: "" });
      setSelectedId(nextSelection(before, after.items, member.member));
      toast.success(decisionToast(answer.member));
    } catch (error) {
      toast.error(error);
      detail.reload();
    } finally {
      setSaving(false);
    }
  };
  const act = {
    provider: (publicName) => save(`${memberPath(member.member)}/decision`, { provider: true, public_name: publicName }),
    notProvider: () => member?.status !== "not_provider" && save(`${memberPath(member.member)}/decision`, { provider: false }),
    clear: () => member?.status !== "undecided" && save(`${memberPath(member.member)}/clear`),
  };

  useShortcuts([
    { keys: "p", label: "Type the public name (Enter saves: a provider)", run: () => document.getElementById(NAME_FIELD_ID)?.focus() },
    { keys: "n", label: "Not a provider", run: act.notProvider },
    { keys: "c", label: "Clear the decision (undecided again)", run: act.clear },
  ]);

  const listPanel = (
    <PanelState loading={list.loading} error={list.error} hasData={Boolean(list.data)} onRetry={list.reload} empty={items.length === 0} emptyText={emptyListText(show)}>
      <ItemList items={items} getKey={(row) => row.member} selectedKey={selectedId} onSelect={setSelectedId} renderItem={(row) => <MemberRow row={row} />} />
    </PanelState>
  );

  return (
    <Layout
      toolId={tool.id}
      toolbar={<Toolbar show={show} onShow={setShow} summary={summary.data} />}
      list={listPanel}
      main={<MemberDetail detail={{ ...detail, data: member }} selectedId={selectedId} onPick={(picked) => setDraft({ member: selectedId, name: picked })} />}
      side={
        <DecisionPanel
          detail={{ data: member }}
          aliasExample={summary.data?.alias_example}
          name={name}
          onName={(typed) => setDraft({ member: selectedId, name: typed })}
          saving={saving}
          onProvider={act.provider}
          onNotProvider={act.notProvider}
          onClear={act.clear}
        />
      }
      listWidth={{ initial: 340 }}
    />
  );
}

export default { id: TOOL_ID, title: "Tagged members", Component: TaggedTool };
