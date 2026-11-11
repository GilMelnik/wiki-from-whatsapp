import { Bidi, Ltr, stanceOf } from "@shell";
import { ClaimText } from "./ClaimText.jsx";

// One claim under a member: pick box, highlighted text, the facts around it (id, stance,
// whether the claim tags the name or only mentions it, other entities), and its actions.

function EntityChips({ label, entities, tagged, onOpenEntity }) {
  if (entities.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {label}
      {entities.map((entity) =>
        entity.entity_id ? (
          <button
            key={entity.entity_id}
            type="button"
            className={`badge badge-slate hover:bg-slate-200 ${tagged ? "border border-dashed border-slate-400" : ""}`}
            title={tagged ? "Tagged in the claim without the name appearing in the text" : "Open the entity"}
            onClick={() => onOpenEntity(entity.entity_id)}
          >
            <Bidi>{entity.canonical_name}</Bidi>
          </button>
        ) : (
          <Bidi
            key={entity.name}
            className="badge border border-dashed border-slate-300 text-slate-500"
            title="Tagged in the claim; it has no entity"
          >
            {entity.name}
          </Bidi>
        )
      )}
    </span>
  );
}

export function ClaimItem({ claim, memberIndex, picked, saving, onTogglePick, onOpenEntity, onExclude, onDelete, onShowAggregation }) {
  return (
    <li className="flex items-start gap-2 rounded bg-white/80 p-2">
      <input
        type="checkbox"
        className="mt-1 shrink-0"
        aria-label={`Pick claim ${claim.claim_id}`}
        checked={picked}
        onChange={onTogglePick}
      />
      <div className="min-w-0 flex-1 space-y-1">
        <ClaimText
          text={claim.claim_text}
          highlights={claim.highlights}
          memberIndex={memberIndex}
          onOpenEntity={onOpenEntity}
        />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          <Ltr>{claim.claim_id}</Ltr>
          {claim.stance && <span>{stanceOf(claim.stance).label}</span>}
          {!claim.tagged && (
            <span className="badge badge-sky" title="The claim is not tagged with this name; the name only appears in its text">
              Text mention only
            </span>
          )}
          {claim.aggregation && (
            <button type="button" className="badge badge-sky hover:bg-sky-200" onClick={onShowAggregation}>
              Combines {claim.aggregation.count} claims
            </button>
          )}
          <EntityChips label="Other entities:" entities={claim.other_entities} onOpenEntity={onOpenEntity} />
          <EntityChips label="Tagged without a mention:" entities={claim.related_entities} tagged onOpenEntity={onOpenEntity} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        <button
          type="button"
          className="btn btn-sm"
          disabled={saving}
          title="The claim does not belong to this name in this entity"
          onClick={onExclude}
        >
          Unrelated
        </button>
        <button
          type="button"
          className="btn btn-sm text-rose-700"
          disabled={saving}
          title="Delete the claim from the whole pipeline"
          onClick={onDelete}
        >
          Delete
        </button>
      </div>
    </li>
  );
}
