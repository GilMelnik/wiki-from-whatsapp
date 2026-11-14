import { useEffect, useState } from "react";
import { Bidi, countText, useResource } from "@shell";
import { api, entityPath } from "./api.js";
import { ClaimItem } from "./ClaimItem.jsx";
import { colour, moreShown, SIGNAL_LABELS } from "./entities.js";

// One name (one spelling) of the entity: does it belong here? Its claims (the first three
// come with the entity; "Show more" loads more), how step 4 attached it, the other entities
// that hold the same name, and the claims excluded from it. Actions on picked claims appear
// once a claim is picked.

function AttachedNotice({ attachedBy, saving, onConfirm, onMove }) {
  const signals = attachedBy.signals.map((signal) => SIGNAL_LABELS[signal] ?? signal).join(", ");
  return (
    <div className="note note-warning flex flex-wrap items-center gap-2 p-2 text-xs">
      <span title={`Similarity ${attachedBy.similarity}`}>
        An update attached this name because it looks like “<Bidi className="font-semibold">{attachedBy.matched}</Bidi>”
        {signals && ` (${signals})`}. Does it belong here?
      </span>
      <span className="ms-auto flex gap-1">
        <button type="button" className="btn btn-sm" disabled={saving} onClick={onConfirm}>
          It belongs here
        </button>
        <button type="button" className="btn btn-sm" disabled={saving} onClick={onMove}>
          Move it…
        </button>
      </span>
    </div>
  );
}

function Excluded({ claims, saving, onInclude }) {
  if (claims.length === 0) return null;
  return (
    <details className="text-xs text-slate-600">
      <summary className="cursor-pointer">{countText(claims.length, "claim")} marked as unrelated to this name</summary>
      <ul className="mt-1 space-y-1">
        {claims.map((claim) => (
          <li key={claim.claim_id} className="flex items-start gap-2 rounded bg-white/70 p-1">
            <Bidi className="flex-1">{claim.claim_text || claim.claim_id}</Bidi>
            <button type="button" className="btn btn-sm" disabled={saving} onClick={() => onInclude([claim.claim_id])}>
              Return
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function PickedActions({ name, picked, saving, on }) {
  if (picked.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1 text-xs">
      <span className="text-slate-600">{countText(picked.length, "claim")} picked:</span>
      <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.claims("copy", name, picked)}>
        Copy…
      </button>
      <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.claims("move", name, picked)}>
        Move…
      </button>
      <button type="button" className="btn btn-sm text-rose-700" disabled={saving} onClick={() => on.exclude(name, picked)}>
        Unrelated
      </button>
    </span>
  );
}

export function MemberCard({ entityId, member, picked, saving, reads, on }) {
  const first = member.claims;
  const [shown, setShown] = useState(first.claims.length);
  const more = useResource(
    () => (shown <= first.claims.length ? null : api.get(`${entityPath(entityId)}/claims`, { name: member.name, offset: 0, limit: shown })),
    [shown],
    { watch: reads, keepPrevious: true }
  );
  const claims = (shown > first.claims.length && more.data) || first;
  // The name lost claims under what is shown (moved, excluded): show the first ones again.
  useEffect(() => {
    if (more.data && shown > more.data.count) setShown(first.claims.length);
  }, [more.data]);
  const tone = colour(member.member_index);
  const name = member.name;
  const left = claims.count - claims.claims.length;

  return (
    <section className={`space-y-2 rounded-lg border p-3 ${tone.card}`}>
      <header className="flex flex-wrap items-center gap-2">
        <span className={`h-3 w-3 shrink-0 rounded-full ${tone.dot}`} />
        <Bidi className="font-semibold">{name}</Bidi>
        <span className="text-xs text-slate-500">{countText(claims.count, "claim")}</span>
        {member.is_canonical && <span className="badge badge-emerald">Canonical name</span>}
        <span className="ms-auto flex flex-wrap gap-1">
          {!member.is_canonical && (
            <button type="button" className="btn btn-sm btn-quiet" disabled={saving} onClick={() => on.setCanonical(name)}>
              Make canonical
            </button>
          )}
          <button type="button" className="btn btn-sm btn-quiet" disabled={saving} onClick={() => on.moveMember(name)}>
            Move name…
          </button>
        </span>
      </header>
      {member.attached_by && (
        <AttachedNotice attachedBy={member.attached_by} saving={saving} onConfirm={() => on.confirm(name)} onMove={() => on.moveMember(name)} />
      )}
      {member.also_in.length > 0 && (
        <p className="flex flex-wrap items-center gap-1 text-xs text-slate-600">
          This name also belongs to:
          {member.also_in.map((other) => (
            <button key={other.entity_id} type="button" className="badge badge-slate hover:bg-slate-200" onClick={() => on.open(other.entity_id)}>
              <Bidi>{other.canonical_name}</Bidi>
            </button>
          ))}
        </p>
      )}
      <PickedActions name={name} picked={picked} saving={saving} on={on} />
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
        {claims.claims.length === 0 && <li className="p-2 text-xs text-slate-500">No active claims for this name.</li>}
      </ul>
      {left > 0 && (
        <button type="button" className="btn btn-sm btn-quiet" disabled={more.loading && shown > first.claims.length} onClick={() => setShown(moreShown(claims.claims.length, claims.count))}>
          Show more ({countText(left, "claim")} not shown)
        </button>
      )}
      <Excluded claims={member.excluded_claims} saving={saving} onInclude={(ids) => on.include(name, ids)} />
    </section>
  );
}
