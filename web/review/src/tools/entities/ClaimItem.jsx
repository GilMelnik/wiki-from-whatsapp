import { Bidi, Ltr } from "@shell";
import { ClaimText } from "./ClaimText.jsx";
import { STANCE_LABELS } from "./entities.js";

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
            title={tagged ? "מסומנת בטענה בלי שהשם מופיע בטקסט" : "פתיחת הישות"}
            onClick={() => onOpenEntity(entity.entity_id)}
          >
            <Bidi>{entity.canonical_name}</Bidi>
          </button>
        ) : (
          <Bidi
            key={entity.name}
            className="badge border border-dashed border-slate-300 text-slate-500"
            title="מסומנת בטענה, אין לה ישות"
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
        aria-label={`בחירת הטענה ${claim.claim_id}`}
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
          {claim.stance && <span>{STANCE_LABELS[claim.stance] ?? claim.stance}</span>}
          {!claim.tagged && (
            <span className="badge badge-sky" title="הטענה לא מסומנת בשם הזה, השם רק מופיע בטקסט שלה">
              אזכור בטקסט בלבד
            </span>
          )}
          {claim.aggregation && (
            <button type="button" className="badge badge-sky hover:bg-sky-200" onClick={onShowAggregation}>
              מאחדת {claim.aggregation.count} טענות
            </button>
          )}
          <EntityChips label="ישויות נוספות:" entities={claim.other_entities} onOpenEntity={onOpenEntity} />
          <EntityChips label="מסומנות בלי אזכור:" entities={claim.related_entities} tagged onOpenEntity={onOpenEntity} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col gap-1">
        <button
          type="button"
          className="btn btn-sm"
          disabled={saving}
          title="הטענה לא שייכת לשם הזה בישות הזאת"
          onClick={onExclude}
        >
          לא קשורה
        </button>
        <button
          type="button"
          className="btn btn-sm text-rose-700"
          disabled={saving}
          title="מחיקת הטענה מכל הצנרת"
          onClick={onDelete}
        >
          מחיקה
        </button>
      </div>
    </li>
  );
}
