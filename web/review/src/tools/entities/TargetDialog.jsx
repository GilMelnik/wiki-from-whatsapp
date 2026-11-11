import { useEffect, useState } from "react";
import { Bidi, countText, Dialog, PanelState, useResource } from "@shell";
import { api } from "./api.js";
import { SEARCH_DEBOUNCE_MS, TARGET_SEARCH_LIMIT } from "./entities.js";

// Choosing the entity a merge, a moved name or moved / copied claims go to: search by name,
// or (when allowed) a new entity. Arrow keys in the search field move the choice, Enter
// confirms. `suggested` entities (for example step 4's look-alikes) are listed first.

const NEW = "__new__";

function Option({ chosen, onChoose, title, subtitle }) {
  return (
    <li>
      <button
        type="button"
        onClick={onChoose}
        className={`list-row rounded ${chosen ? "list-row-selected" : ""}`}
        aria-pressed={chosen}
      >
        <span className="block font-medium">{title}</span>
        {subtitle && <span className="block text-xs text-slate-500">{subtitle}</span>}
      </button>
    </li>
  );
}

function Description({ entity }) {
  if (entity.member_count === undefined) return null; // a look-alike reference: name only
  return (
    <>
      {countText(entity.member_count, "name")} · {countText(entity.total_count, "claim")}
      {entity.aliases.length > 1 && (
        <>
          {" · "}
          <Bidi>{entity.aliases.join(" · ")}</Bidi>
        </>
      )}
    </>
  );
}

export function TargetDialog({ title, description, allowNew, excludeId, suggested = [], initialTarget, confirmLabel, onClose, onConfirm }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [choice, setChoice] = useState(initialTarget ?? undefined);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);
  const results = useResource(
    () => api.get("/entities", { q: search, limit: TARGET_SEARCH_LIMIT, sort: "count", order: "desc" }),
    [search],
    { keepPrevious: true }
  );
  const suggestedIds = new Set(suggested.map((entity) => entity.entity_id));
  const found = (results.data?.items ?? []).filter(
    (entity) => entity.entity_id !== excludeId && !suggestedIds.has(entity.entity_id)
  );
  const options = [
    ...(allowNew ? [{ key: NEW }] : []),
    ...suggested.map((entity) => ({ key: entity.entity_id, entity, hint: "Looks alike (step 4)" })),
    ...found.map((entity) => ({ key: entity.entity_id, entity })),
  ];
  const step = (delta) => {
    const index = options.findIndex((option) => option.key === choice);
    const next = options[Math.min(options.length - 1, Math.max(0, index + delta))];
    if (next) setChoice(next.key);
  };

  return (
    <Dialog
      open
      size="lg"
      title={title}
      confirmLabel={confirmLabel}
      confirmDisabled={choice === undefined}
      onClose={onClose}
      onConfirm={() => onConfirm(choice === NEW ? null : choice)}
    >
      {description && <p className="mb-3 text-slate-600">{description}</p>}
      <input
        type="search"
        dir="auto"
        className="field mb-3 w-full"
        placeholder="Search entities by name or alias…"
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
          {options.map((option) =>
            option.key === NEW ? (
              <Option key={NEW} chosen={choice === NEW} onChoose={() => setChoice(NEW)} title="+ New entity" subtitle="A new, separate entity, accepted" />
            ) : (
              <Option
                key={option.key}
                chosen={choice === option.key}
                onChoose={() => setChoice(option.key)}
                title={
                  <>
                    <Bidi>{option.entity.canonical_name}</Bidi>
                    {option.hint && <span className="badge badge-amber ms-2">{option.hint}</span>}
                  </>
                }
                subtitle={<Description entity={option.entity} />}
              />
            )
          )}
          {options.length === 0 && <li className="p-2 text-slate-500">No entities found.</li>}
        </ul>
      </PanelState>
    </Dialog>
  );
}
