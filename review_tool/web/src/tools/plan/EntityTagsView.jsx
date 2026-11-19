import { Bidi, claimCount, countText, formatCount, Ltr, Panel, PanelState } from "@shell";
import { countRows, DECISION_LABELS, SOURCE_NOTES, splitEntities } from "./entityTags.js";

// Main column when "Entity tags" is selected: the owner's rule for the site's entity tags (an
// entity named by enough claims, cited on enough pages, that no page or label of the plan
// covers gets a tag page), its numbers, and a yes / no on each entity whose name only partly
// matches a page or label of the plan: covered (no tag) or make a tag. Until the owner decides,
// such an entity gets no tag. Names and titles are Hebrew content; everything else is English.

function Numbers({ view }) {
  return (
    <div className="space-y-2">
      <table className="text-sm">
        <tbody>
          {countRows(view).map((row) => (
            <tr key={row.key}>
              <td className="pe-4 text-slate-600">{row.label}</td>
              <td className="text-end font-medium tabular-nums">{formatCount(row.count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-slate-500">{SOURCE_NOTES[view.source]}</p>
      <details>
        <summary className="cursor-pointer text-slate-600">Entities named by at least N claims</summary>
        <table className="mt-2 text-sm">
          <tbody>
            {view.table.map((row) => (
              <tr key={row.min_claims}>
                <td className="pe-4 text-slate-600">N = {formatCount(row.min_claims)}</td>
                <td className="text-end tabular-nums">{countText(row.entities, "entity", formatCount, "entities")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

function Entity({ entity, busy, onDecide }) {
  const others = entity.names.filter((name) => name !== entity.name);
  return (
    <li className="card space-y-2" title={entity.entity_id}>
      <div className="flex flex-wrap items-center gap-2">
        <Bidi className="font-medium">{entity.name}</Bidi>
        <span className="text-xs text-slate-500">
          {claimCount(entity.claim_count, formatCount)} · {countText(entity.pages.length, "page", formatCount)}
        </span>
        {entity.decision && <span className="badge badge-slate">{DECISION_LABELS[entity.decision]}</span>}
      </div>
      {others.length > 0 && (
        <p className="text-xs text-slate-500">
          Also named: <Bidi>{others.join(" · ")}</Bidi>
        </p>
      )}
      <ul className="space-y-1">
        {entity.matches.map((match) => (
          <li key={`${match.kind}:${match.id}`}>
            “<Bidi>{match.entity_name}</Bidi>” partly matches the {match.kind} “<Bidi>{match.plan_name}</Bidi>”
            {match.plan_name !== match.title && (
              <>
                {" "}
                (<Bidi>{match.title}</Bidi>)
              </>
            )}{" "}
            <Ltr className="text-xs text-slate-500">{match.id}</Ltr>
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-500">
        Cited on: <Bidi>{entity.pages.map((page) => page.title).join(" · ")}</Bidi>
      </p>
      <div className="flex flex-wrap gap-2">
        {["covered", "tag"].map((decision) => (
          <button
            key={decision}
            type="button"
            className="btn btn-sm"
            disabled={busy || entity.decision === decision}
            onClick={() => onDecide(entity, decision)}
          >
            {decision === "covered" ? "Covered: no tag" : "Make a tag"}
          </button>
        ))}
        {entity.decision && (
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onDecide(entity, null)}>
            Undecide
          </button>
        )}
      </div>
    </li>
  );
}

/** The rule, its numbers and the entities to decide on (`tags` is the view's resource). */
export function EntityTagsView({ tags, busy, onDecide }) {
  const view = tags.data;
  const { toDecide, decided } = splitEntities(view);
  return (
    <Panel
      title="Entity tags"
      subtitle={
        view?.thresholds &&
        `An entity named by at least ${view.thresholds.min_claims} claims and cited on at least ${view.thresholds.min_pages} published pages gets a tag page listing those pages, unless a page or label of the plan already covers it.`
      }
    >
      <PanelState loading={tags.loading} error={tags.error} hasData={Boolean(view)} onRetry={tags.reload}>
        {view?.problem ? (
          <p className="note note-warning m-4">{view.problem}</p>
        ) : (
          view && (
            <div className="max-w-4xl space-y-6 p-4 text-sm">
              <Numbers view={view} />
              <section>
                <h3 className="section-title">To decide: is the entity covered by the plan?</h3>
                {toDecide.length === 0 ? (
                  <p className="text-slate-500">No entity waits for a decision.</p>
                ) : (
                  <ul className="space-y-2">
                    {toDecide.map((entity) => (
                      <Entity key={entity.entity_id} entity={entity} busy={busy} onDecide={onDecide} />
                    ))}
                  </ul>
                )}
              </section>
              {decided.length > 0 && (
                <details>
                  <summary className="cursor-pointer text-slate-600">Decided ({formatCount(decided.length)})</summary>
                  <ul className="mt-2 space-y-2">
                    {decided.map((entity) => (
                      <Entity key={entity.entity_id} entity={entity} busy={busy} onDecide={onDecide} />
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )
        )}
      </PanelState>
    </Panel>
  );
}
