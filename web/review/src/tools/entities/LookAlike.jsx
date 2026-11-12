import { Bidi, countText, Kbd } from "@shell";
import { ClaimText } from "./ClaimText.jsx";
import { claimTip, statusOf } from "./entities.js";

// The decision "is it the same entity as its look-alike?", side by side: this entity and the
// one step 4 kept apart from it (their context differs), each with its names and the first
// few claims of each name. Same: merge into it. Different: accept this entity, which clears
// the flag.

function Side({ title, entity, members, onOpenEntity, actions }) {
  return (
    <div className="card min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">{title}</span>
        <Bidi className="font-semibold">{entity.canonical_name || "(no name)"}</Bidi>
        {entity.status !== "suggested" && <span className={`badge ${statusOf(entity.status).badge}`}>{statusOf(entity.status).label}</span>}
        {actions}
      </div>
      {members.map((member) => (
        <div key={member.name} className="space-y-1">
          <p className="text-xs">
            <Bidi className="font-medium text-slate-800">{member.name}</Bidi>{" "}
            <span className="text-slate-500">· {countText(member.claims.count, "claim")}</span>
          </p>
          <ul className="space-y-1 border-s-2 border-slate-200 ps-2">
            {member.claims.claims.map((claim) => (
              <li key={claim.claim_id} title={claimTip(claim)}>
                <ClaimText text={claim.claim_text} highlights={claim.highlights} memberIndex={member.member_index ?? 0} onOpenEntity={onOpenEntity} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function GoneLookAlikes({ names }) {
  if (names.length === 0) return null;
  return (
    <p className="note note-warning text-xs">
      Step 4 flagged it as like {names.map((name, index) => (
        <span key={name}>
          {index > 0 && ", "}“<Bidi>{name}</Bidi>”
        </span>
      ))}
      , which no entity holds any more. Accept it (<Kbd>a</Kbd>) to clear the flag.
    </p>
  );
}

export function LookAlikes({ entity, saving, on }) {
  const gone = entity.conflicts.filter((conflict) => !conflict.entity_id).map((conflict) => conflict.name);
  if (entity.look_alikes.length === 0) return <GoneLookAlikes names={gone} />;
  return (
    <section className="space-y-3">
      <GoneLookAlikes names={gone} />
      <h3 className="section-title">Is it the same as {entity.look_alikes.length === 1 ? "this entity" : "one of these entities"}?</h3>
      <p className="text-xs text-slate-600">
        Step 4 kept them apart because the claims around them differ. Same: merge this one into it. Different: accept this
        entity (<Kbd>a</Kbd>) and the flag goes away.
      </p>
      {entity.look_alikes.map((other) => (
        <div key={other.entity_id} className="grid gap-3 lg:grid-cols-2">
          <Side title="This entity" entity={entity} members={entity.members} onOpenEntity={on.open} />
          <Side
            title="Looks like"
            entity={other}
            members={other.members}
            onOpenEntity={on.open}
            actions={
              <span className="ms-auto flex gap-1">
                <button type="button" className="btn btn-sm" onClick={() => on.open(other.entity_id)}>
                  Open it
                </button>
                <button type="button" className="btn btn-sm btn-primary" disabled={saving} onClick={() => on.mergeInto(other.entity_id)}>
                  Same: merge into it…
                </button>
              </span>
            }
          />
        </div>
      ))}
    </section>
  );
}
