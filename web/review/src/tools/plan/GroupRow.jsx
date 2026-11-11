import { Bidi, countText, formatCount, Ltr, stanceOf, titleOf } from "@shell";
import { percent } from "./plan.js";

// One claim group in an assignment list: its text, stance, support, last date, labels, the
// classifier's probability for this page / label, where else it is, a mark for groups the
// owner pinned by hand and for ids the next assignment run will change. A probability under
// the step's own bar (its decision rules, from the server) is marked. Clicking the row (or its
// box) picks it for a bulk move.

function Quoted({ overview, id }) {
  return <>“<Bidi>{titleOf(overview, id)}</Bidi>”</>;
}

function Where({ row, overview, scope, itemId }) {
  if (!row.record) return <span className="badge badge-amber">New: not assigned yet</span>;
  const pages = row.pages ?? [];
  if (pages.length === 0) return scope === "unplaced" ? null : <span className="badge badge-slate">On no page</span>;
  if (scope === "page") {
    if (row.role === "mention") return <span>Primary page: <Quoted overview={overview} id={pages[0]} /></span>;
    return pages[1] ? <span>Also on <Quoted overview={overview} id={pages[1]} /></span> : null;
  }
  return (
    <span>
      On <Quoted overview={overview} id={pages[0]} />
      {pages[1] && <> · also <Quoted overview={overview} id={pages[1]} /></>}
    </span>
  );
}

function Waiting({ row }) {
  const moved = Object.entries(row.redirected ?? {});
  const dropped = row.dropped ?? [];
  if (moved.length === 0 && dropped.length === 0) return null;
  const title = "These ids are no longer in the plan; the next assignment run will update the group";
  return (
    <span className="badge badge-amber" title={title}>
      Waiting for the assignment run:{" "}
      {moved.map(([from, to]) => (
        <Ltr key={from} className="me-1">{`${from}→${to}`}</Ltr>
      ))}
      {dropped.map((id) => (
        <Ltr key={id} className="me-1 line-through">{id}</Ltr>
      ))}
    </span>
  );
}

export function GroupRow({ row, overview, scope, itemId, picked, onToggle }) {
  const stance = stanceOf(row.stance);
  const thresholds = overview.assignments?.thresholds ?? {};
  const bar = scope === "label" ? thresholds.label : thresholds.page;
  const labels = (row.labels ?? []).filter((id) => !(scope === "label" && id === itemId));
  const pickable = Boolean(row.record);
  return (
    <li className={`flex gap-3 px-3 py-2 ${picked ? "bg-blue-50" : "hover:bg-slate-50"}`}>
      <input
        type="checkbox"
        className="mt-1 shrink-0"
        checked={picked}
        disabled={!pickable}
        aria-label={`Pick group ${row.group_id}`}
        onChange={onToggle}
      />
      <div className={`min-w-0 flex-1 ${pickable ? "cursor-pointer" : ""}`} onClick={pickable ? onToggle : undefined}>
        {row.missing ? (
          <p className="text-slate-400">The group is no longer in the groups file; the next assignment run will remove it.</p>
        ) : (
          <Bidi as="p" className="leading-relaxed">{row.claim_text}</Bidi>
        )}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          {row.certainty !== null && row.certainty !== undefined && (
            <span
              className={`badge ${row.certainty < bar ? "badge-amber" : "badge-slate"}`}
              title={scope === "unplaced" ? "The highest probability the classifier gave any page" : "The probability the classifier gave"}
            >
              {percent(row.certainty)}
            </span>
          )}
          {row.stance && <span className={`badge ${stance.badge}`}>{stance.label}</span>}
          {!row.missing && (
            <span>
              {countText(row.supporter_count ?? 0, "supporter", formatCount)} · {countText(row.opposer_count ?? 0, "opposer", formatCount)}
            </span>
          )}
          {row.last_date && <span>until <Ltr>{row.last_date}</Ltr></span>}
          {row.claim_count > 1 && <span>{countText(row.claim_count, "claim", formatCount)}</span>}
          {row.manual && <span className="badge badge-sky" title="Assigned by hand: the assignment step will not decide it again">Manual</span>}
          <Where row={row} overview={overview} scope={scope} itemId={itemId} />
          {labels.map((id) => (
            <Bidi key={id} className="badge badge-slate">{titleOf(overview, id)}</Bidi>
          ))}
          <Waiting row={row} />
          <Ltr className="text-slate-400">{row.group_id}</Ltr>
        </p>
      </div>
    </li>
  );
}
