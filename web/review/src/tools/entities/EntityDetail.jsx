import { Bidi, formatCount, Ltr, Panel, PanelState } from "@shell";
import { pickedFor, SIGNAL_LABELS, statusOf } from "./entities.js";
import { MemberCard } from "./MemberCard.jsx";

// Main column: the selected entity, what step 4 flagged about it (look-alike entities,
// automatically attached names), and every member with its claims.

function QueueNav({ queue, onSelect }) {
  if (!queue || queue.position === null) return null;
  return (
    <>
      <button type="button" className="btn btn-sm" disabled={!queue.prev_id} onClick={() => onSelect(queue.prev_id)}>
        ‹ הקודמת
      </button>
      <span className="text-xs text-slate-500">
        {formatCount(queue.position + 1)} / {formatCount(queue.total)}
      </span>
      <button type="button" className="btn btn-sm" disabled={!queue.next_id} onClick={() => onSelect(queue.next_id)}>
        הבאה ›
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
            שלב 4 החזיק את הישות הזאת בנפרד מישויות שנראות דומות, כי ההקשר שלהן שונה. האם זו
            אותה ישות?
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
                      מיזוג לתוכה…
                    </button>
                  </>
                ) : (
                  <Bidi className="badge badge-slate" title="אין כבר ישות בשם הזה">
                    {conflict.name}
                  </Bidi>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs">אם אלה ישויות שונות, אשרו את הישות (a) והסימון יוסר.</p>
        </div>
      )}
      {attached > 0 && (
        <p className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
          {attached === 1 ? "שם אחד צורף" : `${attached} שמות צורפו`} לישות אוטומטית בעדכון של שלב 4.
          בדקו את הטענות שלו ואשרו את השיוך, או העבירו את השם לישות אחרת.
        </p>
      )}
    </>
  );
}

export function EntityDetail({ detail, selectedId, picked, saving, reads, on }) {
  if (!selectedId) return <PanelState empty emptyText="בחרו ישות מהרשימה" />;
  const entity = detail.data?.entity;
  const status = entity && statusOf(entity.status);
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(entity)} onRetry={detail.reload}>
      {entity && (
        <Panel
          title={<Bidi>{entity.canonical_name || "(ללא שם)"}</Bidi>}
          subtitle={
            <span className="flex flex-wrap items-center gap-2">
              <span className={`badge ${status.badge}`}>{status.label}</span>
              <span>
                {formatCount(entity.member_count)} שמות · {formatCount(entity.total_count)} טענות
              </span>
              <Ltr>{entity.entity_id}</Ltr>
              {detail.data.queue.position === null && <span className="badge badge-slate">לא ברשימה המסוננת</span>}
              {entity.type && <span className="badge badge-sky">{entity.type}</span>}
              {entity.merge_signals.map((signal) => (
                <span key={signal} className="badge badge-slate" title="למה שלב 4 איחד את השמות">
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
