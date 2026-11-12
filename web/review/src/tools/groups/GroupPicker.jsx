import { useEffect, useState } from "react";
import { Bidi, Dialog, PanelState, useResource } from "@shell";
import { api } from "./api.js";
import { GroupBadges, GroupNumbers } from "./GroupRow.jsx";
import { PICKER_LIMIT, SEARCH_DEBOUNCE_MS } from "./groups.js";

// Choosing the other group of a merge, a move or a contradiction link: search by text,
// entity or id; `suggested` groups (merge suggestions for this group) are listed first.
// Arrow keys in the search field move the choice, Enter confirms. `notes(choice)` shows what
// the choice would mean (a merge: a dispute, a withheld group released) under the list, and
// `confirmFor(choice)` may word the confirm button for it.

function Option({ group, chosen, hint, onChoose }) {
  return (
    <li>
      <button type="button" title={group.group_id} onClick={onChoose} aria-pressed={chosen} className={`list-row rounded ${chosen ? "list-row-selected" : ""}`}>
        <Bidi as="span" className="line-clamp-2 block">
          {group.claim_text}
        </Bidi>
        <span className="flex flex-wrap items-center gap-2">
          {hint && <span className="badge badge-sky">{hint}</span>}
          <GroupBadges group={group} quiet />
          <GroupNumbers group={group} />
        </span>
      </button>
    </li>
  );
}

export function GroupPicker({ title, description, excludeId, suggested = [], initial, confirmLabel, confirmFor, notes, tone, onClose, onConfirm }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [choice, setChoice] = useState(initial ?? suggested[0]?.group_id);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);
  const results = useResource(
    () => api.get("/groups", { q: search, limit: PICKER_LIMIT, sort: "size", order: "desc" }),
    [search],
    { keepPrevious: true }
  );
  const shown = new Set(suggested.map((group) => group.group_id));
  const options = [
    ...suggested.map((group) => ({ group, hint: "Suggested for this group" })),
    ...(results.data?.items ?? [])
      .filter((group) => group.group_id !== excludeId && !shown.has(group.group_id))
      .map((group) => ({ group })),
  ];
  const step = (delta) => {
    const index = options.findIndex((option) => option.group.group_id === choice);
    const next = options[Math.min(options.length - 1, Math.max(0, index + delta))];
    if (next) setChoice(next.group.group_id);
  };
  return (
    <Dialog
      open
      size="lg"
      title={title}
      tone={tone}
      confirmLabel={(choice && confirmFor?.(choice)) || confirmLabel}
      confirmDisabled={!choice}
      onClose={onClose}
      onConfirm={() => onConfirm(choice)}
    >
      {description && <p className="mb-3 text-slate-600">{description}</p>}
      <input
        type="search"
        dir="auto"
        className="field mb-3 w-full"
        placeholder="Search by text, entity or id…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            step(event.key === "ArrowDown" ? 1 : -1);
          }
        }}
      />
      <PanelState loading={results.loading} error={results.error} hasData={Boolean(results.data)} onRetry={results.reload}>
        <ul className="space-y-1">
          {options.map((option) => (
            <Option
              key={option.group.group_id}
              group={option.group}
              hint={option.hint}
              chosen={choice === option.group.group_id}
              onChoose={() => setChoice(option.group.group_id)}
            />
          ))}
          {options.length === 0 && <li className="p-2 text-slate-500">No groups found.</li>}
        </ul>
      </PanelState>
      {choice && notes && <div className="mt-3">{notes(choice)}</div>}
    </Dialog>
  );
}
