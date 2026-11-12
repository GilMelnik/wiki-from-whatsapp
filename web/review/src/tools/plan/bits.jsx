import { Bidi, Ltr, titleOf } from "@shell";
import { changeOf, warningsFor } from "./plan.js";

// Small pieces shared by the plan tool's rows and views.

const FIELD_LABELS = {
  title_he: "title",
  description: "description",
  scope: "scope",
  aliases: "aliases",
  path: "place",
  parent_id: "place",
  kind: "kind",
};

/** "New" / "Edited" next to an item the owner changed since the model's proposal. */
export function ChangeBadge({ overview, id }) {
  const change = changeOf(overview, id);
  if (!change) return null;
  if (change === "new") return <span className="badge badge-sky">New</span>;
  const fields = [...new Set(change.map((name) => FIELD_LABELS[name] ?? name))];
  return (
    <span className="badge badge-sky" title={`Changed since the proposal: ${fields.join(", ")}`}>
      Edited
    </span>
  );
}

const WARNING_TONES = { warning: "note-warning", info: "note-plain" };

/** The warnings that name `id`, each with links to the other items it names. */
export function Warnings({ overview, id, onSelect }) {
  const list = warningsFor(overview, id);
  if (list.length === 0) return null;
  return (
    <ul className="space-y-2">
      {list.map((warning) => (
        <li key={`${warning.code}:${warning.ids.join(",")}`} className={`note ${WARNING_TONES[warning.tone] ?? "note-warning"}`}>
          <p>{warning.text}</p>
          {warning.ids.some((other) => other !== id) && (
            <p className="mt-1 flex flex-wrap gap-1">
              {warning.ids
                .filter((other) => other !== id)
                .map((other) => (
                  <button key={other} type="button" className="badge badge-amber hover:underline" onClick={() => onSelect(other)}>
                    <Bidi>{titleOf(overview, other)}</Bidi>
                  </button>
                ))}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Ids this item absorbed (merged in, or its own former ids): they redirect here. */
export function MergedFrom({ ids }) {
  if (!ids?.length) return null;
  return (
    <p className="text-xs text-slate-500">
      Old ids that redirect here:{" "}
      {ids.map((old) => (
        <Ltr key={old} className="me-1">
          {old}
        </Ltr>
      ))}
    </p>
  );
}

/** A row of tabs (`tabs` = [{id, label}]); the chosen one is `value`. */
export function Tabs({ tabs, value, onChange, className = "" }) {
  return (
    <div className={`flex w-fit rounded border border-slate-300 ${className}`} role="tablist">
      {tabs.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={value === option.id}
          className={`px-3 py-1 text-sm ${value === option.id ? "bg-blue-600 text-white" : "bg-white hover:bg-slate-100"}`}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
