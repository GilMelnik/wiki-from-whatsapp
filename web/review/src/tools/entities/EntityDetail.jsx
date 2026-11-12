import { Bidi, Panel, PanelState } from "@shell";
import { ContactsReview } from "./Contacts.jsx";
import { pickedFor, SIGNAL_LABELS, statusOf } from "./entities.js";
import { LookAlikes } from "./LookAlike.jsx";
import { MemberCard } from "./MemberCard.jsx";

// Main column: the selected entity, in the order of its decisions: is it the same as the
// look-alike step 4 kept apart from it (side by side), which contacts are right (each with
// the claim it came from), and does each name belong (each name with its first claims).

function Subtitle({ entity, outsideView }) {
  const status = statusOf(entity.status);
  const signals = entity.merge_signals.map((signal) => SIGNAL_LABELS[signal] ?? signal);
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {entity.status !== "suggested" && <span className={`badge ${status.badge}`}>{status.label}</span>}
      {signals.length > 0 && <span>Grouped by: {signals.join(", ").toLocaleLowerCase("en-GB")}</span>}
      {entity.low_cohesion && <span className="text-amber-800">The names are far apart: check each one belongs</span>}
      {outsideView && <span className="badge badge-slate">Not in the filtered list</span>}
    </span>
  );
}

export function EntityDetail({ detail, selectedId, picked, saving, reads, on }) {
  if (!selectedId) return <PanelState empty emptyText="Pick an entity in the list" />;
  const entity = detail.data?.entity;
  return (
    <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(entity)} onRetry={detail.reload}>
      {entity && (
        <Panel
          title={<Bidi title={entity.entity_id}>{entity.canonical_name || "(no name)"}</Bidi>}
          subtitle={<Subtitle entity={entity} outsideView={detail.data.queue.position === null} />}
        >
          <div className="max-w-5xl space-y-6 p-4 text-sm">
            <LookAlikes entity={entity} saving={saving} on={on} />
            <ContactsReview entity={entity} saving={saving} onResolve={on.resolveContact} />
            <section className="space-y-3">
              <h3 className="section-title">{entity.members.length === 1 ? "Its name" : "Its names: does each belong?"}</h3>
              {entity.members.map((member) => (
                <MemberCard
                  key={`${entity.entity_id}\u0001${member.name}`}
                  entityId={entity.entity_id}
                  member={member}
                  picked={pickedFor(picked, entity.entity_id)[member.name] ?? []}
                  saving={saving}
                  reads={reads}
                  on={on}
                />
              ))}
            </section>
          </div>
        </Panel>
      )}
    </PanelState>
  );
}
