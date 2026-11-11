import { Bidi, countText, formatCount, Ltr, Panel, PanelState } from "@shell";
import { pickedFor, SIGNAL_LABELS, statusOf } from "./entities.js";
import { MemberCard } from "./MemberCard.jsx";

// Main column: the selected entity, what step 4 flagged about it (look-alike entities,
// automatically attached names), and every member with its claims.

function QueueNav({ queue, onSelect }) {
  if (!queue || queue.position === null) return null;
  return (
    <>
      <button type="button" className="btn btn-sm" disabled={!queue.prev_id} onClick={() => onSelect(queue.prev_id)}>
        ‹ Previous
      </button>
      <span className="text-xs text-slate-500">
        {formatCount(queue.position + 1)} / {formatCount(queue.total)}
      </span>
      <button type="button" className="btn btn-sm" disabled={!queue.next_id} onClick={() => onSelect(queue.next_id)}>
        Next ›
      </button>
    </>
  );
}

function Flags({ entity, saving, on }) {
  const attached = entity.members.filter((member) => member.attached_by).length;
  return (
    <>
      {entity.conflicts.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
          <p>
            Step 4 kept this entity apart from entities that look alike, because their context
            differs. Is it the same entity?
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {entity.conflicts.map((conflict) => (
              <li key={conflict.name} className="flex items-center gap-1">
                {conflict.entity_id ? (
                  <>
                    <button type="button" className="badge badge-amber hover:bg-amber-200" onClick={() => on.open(conflict.entity_id)}>
                      <Bidi>{conflict.canonical_name}</Bidi>
                    </button>
                    <button type="button" className="btn btn-sm" disabled={saving} onClick={() => on.mergeInto(conflict.entity_id)}>
                      Merge into it…
                    </button>
                  </>
                ) : (
                  <Bidi className="badge badge-slate" title="No entity has this name any more">
                    {conflict.name}
                  </Bidi>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs">If these are different entities, accept this entity (a) and the flag goes away.</p>
        </div>
      )}
      {attached > 0 && (
        <p className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
          {attached === 1 ? "One name was" : `${formatCount(attached)} names were`} attached to this entity
          automatically by a step 4 update. Check the name's claims and confirm the attachment, or
          move the name to another entity.
        </p>
      )}
    </>
  );
}

export function EntityDetail({ detail, selectedId, picked, saving, reads, on }) {
  if (!selectedId) return <PanelState empty emptyText="Choose an entity from the list" />;
  const entity = detail.data?.entity;
  const status = entity && statusOf(entity.status);
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(entity)} onRetry={detail.reload}>
      {entity && (
        <Panel
          title={<Bidi>{entity.canonical_name || "(no name)"}</Bidi>}
          subtitle={
            <span className="flex flex-wrap items-center gap-2">
              <span className={`badge ${status.badge}`}>{status.label}</span>
              <span>
                {countText(entity.member_count, "name")} · {countText(entity.total_count, "claim")}
              </span>
              <Ltr>{entity.entity_id}</Ltr>
              {detail.data.queue.position === null && <span className="badge badge-slate">Not in the filtered list</span>}
              {entity.type && <Bidi className="badge badge-sky">{entity.type}</Bidi>}
              {entity.merge_signals.map((signal) => (
                <span key={signal} className="badge badge-slate" title="Why step 4 grouped the names">
                  {SIGNAL_LABELS[signal] ?? signal}
                </span>
              ))}
            </span>
          }
          actions={<QueueNav queue={detail.data.queue} onSelect={on.open} />}
        >
          <div className="space-y-4 p-4 text-sm">
            <Flags entity={entity} saving={saving} on={on} />
            {entity.members.map((member) => (
              <MemberCard
                key={`${entity.entity_id}\u0001${member.name}`}
                entityId={entity.entity_id}
                member={member}
                pageSize={detail.data.page_size}
                picked={pickedFor(picked, entity.entity_id)[member.name] ?? []}
                saving={saving}
                reads={reads}
                on={on}
              />
            ))}
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
