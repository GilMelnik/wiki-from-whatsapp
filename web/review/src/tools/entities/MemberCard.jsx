import { useEffect, useState } from "react";
import { Bidi, formatCount, Pager, useResource } from "@shell";
import { api, entityPath } from "./api.js";
import { ClaimItem } from "./ClaimItem.jsx";
import { colour, SIGNAL_LABELS } from "./entities.js";

// One member (one spelling) of the entity: its actions, how step 4 attached it, its claims
// (paged by the server; the first page comes with the entity) and the claims excluded from it.

function AttachedNotice({ attachedBy, saving, onConfirm, onMove }) {
  const signals = attachedBy.signals.map((signal) => SIGNAL_LABELS[signal] ?? signal).join(", ");
  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-900">
      <span>
        צורף אוטומטית בעדכון: דומה ל-<Bidi className="font-semibold">{attachedBy.matched}</Bidi> (
        {attachedBy.similarity}
        {signals && `, ${signals}`})
      </span>
      <span className="ms-auto flex gap-1">
        <button type="button" className="btn btn-sm" disabled={saving} onClick={onConfirm}>
          השיוך נכון
        </button>
        <button type="button" className="btn btn-sm" disabled={saving} onClick={onMove}>
          העברה…
        </button>
      </span>
    </div>
  );
}

function Excluded({ claims, saving, onInclude }) {
  if (claims.length === 0) return null;
  return (
    <details className="text-xs text-slate-600">
      <summary className="cursor-pointer">{formatCount(claims.length)} טענות סומנו כלא קשורות לשם הזה</summary>
      <ul className="mt-1 space-y-1">
        {claims.map((claim) => (
          <li key={claim.claim_id} className="flex items-start gap-2 rounded bg-white/70 p-1">
            <Bidi className="flex-1">{claim.claim_text || claim.claim_id}</Bidi>
            <button type="button" className="btn btn-sm" disabled={saving} onClick={() => onInclude([claim.claim_id])}>
              החזרה
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function MemberCard({ entityId, member, pageSize, picked, saving, reads, on }) {
  const [offset, setOffset] = useState(0);
  const page = useResource(
    () =>
      offset === 0
        ? null
        : api.get(`${entityPath(entityId)}/claims`, { name: member.name, offset, limit: pageSize }),
    [offset],
    { watch: reads, keepPrevious: true }
  );
  const claims = (offset > 0 && page.data) || member.claims;
  // The member shrank under the page shown (claims moved or excluded): back to the first page.
  useEffect(() => {
    if (offset > 0 && page.data && offset >= page.data.count) setOffset(0);
  }, [offset, page.data]);
  const tone = colour(member.member_index);
  const none = picked.length === 0;
  const name = member.name;

  return (
    <section className={`space-y-2 rounded-lg border p-3 ${tone.card}`}>
      <header className="flex flex-wrap items-center gap-2">
        <span className={`h-3 w-3 shrink-0 rounded-full ${tone.dot}`} />
        <Bidi className="font-semibold">{name}</Bidi>
        <span className="text-xs text-slate-500">{formatCount(claims.count)} טענות</span>
        {member.is_canonical && <span className="badge badge-emerald">השם הקנוני</span>}
        <span className="ms-auto flex flex-wrap gap-1">
          {!member.is_canonical && (
            <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.setCanonical(name)}>
              קביעה כשם הקנוני
            </button>
          )}
          <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.moveMember(name)}>
            העברת השם…
          </button>
          <button type="button" className="btn btn-sm" disabled={saving || none} onClick={() => on.claims("copy", name, picked)}>
            העתקת טענות ({picked.length})…
          </button>
          <button type="button" className="btn btn-sm" disabled={saving || none} onClick={() => on.claims("move", name, picked)}>
            העברת טענות ({picked.length})…
          </button>
          <button type="button" className="btn btn-sm text-rose-700" disabled={saving || none} onClick={() => on.exclude(name, picked)}>
            לא קשורות ({picked.length})
          </button>
        </span>
      </header>
      {member.attached_by && (
        <AttachedNotice
          attachedBy={member.attached_by}
          saving={saving}
          onConfirm={() => on.confirm(name)}
          onMove={() => on.moveMember(name)}
        />
      )}
      {member.also_in.length > 0 && (
        <p className="flex flex-wrap items-center gap-1 text-xs text-slate-600">
          השם הזה מופיע גם ב:
          {member.also_in.map((other) => (
            <button key={other.entity_id} type="button" className="badge badge-slate hover:bg-slate-200" onClick={() => on.open(other.entity_id)}>
              <Bidi>{other.canonical_name}</Bidi>
            </button>
          ))}
        </p>
      )}
      <ul className="space-y-1.5">
        {claims.claims.map((claim) => (
          <ClaimItem
            key={claim.claim_id}
            claim={claim}
            memberIndex={member.member_index}
            picked={picked.includes(claim.claim_id)}
            saving={saving}
            onTogglePick={() => on.togglePick(name, claim)}
            onOpenEntity={on.open}
            onExclude={() => on.exclude(name, [claim.claim_id])}
            onDelete={() => on.deleteClaim(claim)}
            onShowAggregation={() => on.showAggregation(claim.aggregation)}
          />
        ))}
        {claims.claims.length === 0 && <li className="p-2 text-xs text-slate-500">אין טענות פעילות לשם הזה.</li>}
      </ul>
      <div className="flex justify-center text-xs text-slate-500">
        <Pager offset={claims.offset} limit={pageSize} total={claims.count} onChange={setOffset} />
      </div>
      <Excluded claims={member.excluded_claims} saving={saving} onInclude={(ids) => on.include(name, ids)} />
    </section>
  );
}
